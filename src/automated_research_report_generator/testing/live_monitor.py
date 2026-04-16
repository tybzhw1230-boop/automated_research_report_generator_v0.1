from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

from crewai.events import (
    BaseEventListener,
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
    CrewKickoffStartedEvent,
    FlowFinishedEvent,
    FlowStartedEvent,
    LLMCallCompletedEvent,
    LLMCallFailedEvent,
    LLMCallStartedEvent,
    MethodExecutionFailedEvent,
    MethodExecutionFinishedEvent,
    MethodExecutionStartedEvent,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStartedEvent,
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

from automated_research_report_generator.flow.common import (
    ensure_directory,
    normalize_path,
    read_text_if_exists,
    utc_timestamp,
)
from automated_research_report_generator.testing.live_models import (
    LiveCaseSpec,
    LoopAlert,
    RootCauseCategory,
)

# 设计目的：把子进程内的 CrewAI 事件采集和父进程内的失控监控收口到一个模块。
# 模块功能：一侧负责把关键事件落为 JSONL，另一侧负责基于事件、日志和产物变化执行截停规则。
# 实现逻辑：子进程通过 `BaseEventListener` 记录事件；父进程循环扫描事件文件、日志增长和 checkpoint 心跳。
# 可调参数：上下文错误关键字、轮询周期、重复阈值、日志尾长度和根因分类映射。
# 默认参数及原因：默认采用保守监控阈值，原因是当前目标是先拦住爆 context 和明显循环，再做更细颗粒优化。

EVENT_POLL_INTERVAL_SECONDS = 2.0
CONSECUTIVE_REPEAT_THRESHOLD = 3
TASK_OUTPUT_REPEAT_THRESHOLD = 3
TOOL_REPEAT_THRESHOLD = 3
CHECKPOINT_STALL_SECONDS = 120
LOG_TAIL_CHAR_LIMIT = 12000

CONTEXT_ERROR_KEYWORDS = (
    "context window",
    "context length",
    "maximum context length",
    "too many tokens",
    "token limit",
    "prompt is too long",
    "input exceeds",
    "context exceeded",
)

RATE_LIMIT_KEYWORDS = (
    "rate limit",
    "too many requests",
    "429",
    "quota",
)

_ACTIVE_EVENT_LISTENER: "LiveExecutionEventListener | None" = None


def _normalize_text(value: str) -> str:
    """
    目的：把事件和日志中的文本规整到可稳定比较与匹配关键字的最小形态。
    功能：统一压缩空白、保留原始语义并转成小写字符串。
    实现逻辑：先去掉多余空白，再做小写化，避免日志格式差异影响规则命中。
    可调参数：`value`。
    默认参数及原因：默认不删除标点，原因是错误码和异常名常依赖标点才能准确保留。
    """

    return " ".join(str(value).split()).strip().lower()


def _hash_text(value: str) -> str:
    """
    目的：为重复事件、工具签名和任务输出提供稳定哈希键。
    功能：把任意字符串转换成短 SHA1 摘要，便于写入事件文件和比较。
    实现逻辑：统一按 UTF-8 编码后计算 SHA1，再截取前 12 位。
    可调参数：`value`。
    默认参数及原因：默认使用短哈希，原因是日志里需要可读性，同时冲突风险对当前监控足够低。
    """

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _safe_preview(value: Any, limit: int = 1200) -> str:
    """
    目的：把复杂事件对象里的输出安全收敛成可写入 JSONL 的短文本。
    功能：兼容 `TaskOutput`、字典、列表、异常对象和普通字符串。
    实现逻辑：优先取 `.raw`，其次 JSON 序列化，再退化到 `str()`，最后裁剪长度。
    可调参数：`value` 和 `limit`。
    默认参数及原因：默认裁剪到 1200 字符，原因是监控只需要判断趋势与关键字，不需要保存全量模型输出。
    """

    if value is None:
        return ""
    raw_value = getattr(value, "raw", value)
    if isinstance(raw_value, (dict, list, tuple)):
        try:
            serialized = json.dumps(raw_value, ensure_ascii=False, default=str)
        except Exception:
            serialized = str(raw_value)
    else:
        serialized = str(raw_value)
    return serialized[:limit]


def _classify_text_root_cause(text: str) -> RootCauseCategory:
    """
    目的：基于错误文本、事件预览和日志片段做一次最小启发式根因分类。
    功能：把常见的 context、限流和超时信号归类到固定 root cause 枚举。
    实现逻辑：先匹配 context 关键字，再匹配限流关键字，最后回退到 `unexpected_exception`。
    可调参数：`text`。
    默认参数及原因：默认采用启发式分类，原因是第一版目标是可用的修复 backlog，而不是完美诊断。
    """

    normalized = _normalize_text(text)
    if any(keyword in normalized for keyword in CONTEXT_ERROR_KEYWORDS):
        return "provider_context_overflow"
    if any(keyword in normalized for keyword in RATE_LIMIT_KEYWORDS):
        return "provider_rate_limit"
    if "timeout" in normalized or "stalled" in normalized or "no progress" in normalized:
        return "timeout_no_progress"
    return "unexpected_exception"


def activate_live_event_listener(event_log_path: Path) -> "LiveExecutionEventListener":
    """
    目的：在子进程执行前激活本轮 case 专属的 CrewAI 事件监听器。
    功能：确保事件总线已注册并把关键事件写入当前 case 的 `events.jsonl`。
    实现逻辑：使用模块级单例持有监听器实例，避免被垃圾回收或重复注册多份。
    可调参数：`event_log_path`。
    默认参数及原因：默认每个子进程只保留一个监听器实例，原因是单 case 单子进程不需要多路监听。
    """

    global _ACTIVE_EVENT_LISTENER

    _ACTIVE_EVENT_LISTENER = LiveExecutionEventListener(event_log_path=event_log_path)
    return _ACTIVE_EVENT_LISTENER


class LiveExecutionEventListener(BaseEventListener):
    """
    目的：把 CrewAI 运行期关键事件序列化成稳定 JSONL，供父进程监控和事后分析复用。
    功能：监听 Crew、Task、Tool、LLM、Flow Method 等事件并落盘。
    实现逻辑：在 `setup_listeners()` 里批量注册事件处理器，再统一转成精简结构写文件。
    可调参数：事件输出文件路径和单条事件预览长度。
    默认参数及原因：默认只记录关键事件，原因是当前目标是故障定位，不是构建完整 tracing 平台。
    """

    def __init__(self, *, event_log_path: Path) -> None:
        """
        目的：初始化单个 case 的事件监听器。
        功能：保存事件文件路径并提前创建父目录。
        实现逻辑：只维护必要的文件路径状态，真实注册动作交给基类初始化触发。
        可调参数：`event_log_path`。
        默认参数及原因：默认立即创建父目录，原因是事件可能在 case 很早阶段就开始产生。
        """

        self.event_log_path = Path(event_log_path).expanduser().resolve()
        ensure_directory(self.event_log_path.parent)
        super().__init__()

    def setup_listeners(self, crewai_event_bus: Any) -> None:
        """
        目的：向 CrewAI 事件总线注册本模块关心的关键事件处理器。
        功能：让监控系统能持续收到 Crew、Task、Tool、LLM 和 Flow Method 层的运行脉冲。
        实现逻辑：通过局部 `register()` 工厂函数批量绑定同一套落盘逻辑。
        可调参数：`crewai_event_bus`。
        默认参数及原因：默认不注册过多冷门事件，原因是当前规则只依赖核心执行事件即可工作。
        """

        def register(event_type: Any) -> None:
            @crewai_event_bus.on(event_type)
            def _handler(source: Any, event: Any, *, _event_type: Any = event_type) -> None:
                self._append_event(_event_type.__name__, event)

        for event_type in (
            CrewKickoffStartedEvent,
            CrewKickoffCompletedEvent,
            CrewKickoffFailedEvent,
            TaskStartedEvent,
            TaskCompletedEvent,
            TaskFailedEvent,
            ToolUsageStartedEvent,
            ToolUsageFinishedEvent,
            ToolUsageErrorEvent,
            LLMCallStartedEvent,
            LLMCallCompletedEvent,
            LLMCallFailedEvent,
            FlowStartedEvent,
            FlowFinishedEvent,
            MethodExecutionStartedEvent,
            MethodExecutionFinishedEvent,
            MethodExecutionFailedEvent,
        ):
            register(event_type)

    def _append_event(self, event_name: str, event: Any) -> None:
        """
        目的：把单条 CrewAI 事件转换成可监控的精简 JSON 行。
        功能：提取任务名、角色名、工具名、输出预览、哈希键等核心字段。
        实现逻辑：统一调用字段提取和 key 构造 helper，再按 UTF-8 JSONL 追加落盘。
        可调参数：`event_name` 和 `event`。
        默认参数及原因：默认保留少量预览而非完整负载，原因是父进程监控只需要识别模式，不需要全量 trace。
        """

        preview = self._extract_preview(event)
        tool_name = getattr(event, "tool_name", "") or getattr(event, "tool_class", "")
        tool_args_preview = _safe_preview(getattr(event, "tool_args", ""))
        output_hash = _hash_text(preview) if preview else ""
        tool_signature = (
            f"{tool_name}:{_hash_text(_normalize_text(tool_args_preview))}"
            if tool_name
            else ""
        )
        payload = {
            "recorded_at": utc_timestamp(),
            "event_name": event_name,
            "event_type": getattr(event, "type", event_name),
            "task_name": getattr(event, "task_name", "") or self._safe_name(getattr(event, "task", None)),
            "agent_role": getattr(event, "agent_role", "") or self._safe_role(getattr(event, "agent", None)),
            "tool_name": tool_name,
            "tool_signature": tool_signature,
            "method_name": getattr(event, "method_name", ""),
            "call_type": str(getattr(event, "call_type", "")),
            "preview": preview,
            "output_hash": output_hash,
            "event_key": self._build_event_key(
                event_name=event_name,
                event=event,
                preview=preview,
                output_hash=output_hash,
                tool_signature=tool_signature,
            ),
        }
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _extract_preview(self, event: Any) -> str:
        """
        目的：从不同事件对象中提取最有价值的短文本预览。
        功能：优先抓错误、输出和 LLM 响应，再退化到消息内容或对象字符串。
        实现逻辑：按错误文本、`output`、`response`、`messages` 的优先级顺序提取。
        可调参数：`event`。
        默认参数及原因：默认优先看错误和输出，原因是循环、爆 context 和空转最容易从这些字段识别。
        """

        for attr_name in ("error", "output", "response", "messages"):
            if hasattr(event, attr_name):
                preview = _safe_preview(getattr(event, attr_name))
                if preview:
                    return preview
        return _safe_preview(event)

    def _safe_name(self, value: Any) -> str:
        """
        目的：兼容任务对象可能有不同命名属性的情况，安全提取任务名。
        功能：从对象的 `name` 或 `description` 中得到短标识。
        实现逻辑：优先取 `name`，否则退化为描述文本预览。
        可调参数：`value`。
        默认参数及原因：默认不抛异常，原因是事件监听不应反过来影响业务执行。
        """

        if value is None:
            return ""
        return str(getattr(value, "name", "") or _safe_preview(getattr(value, "description", "")))

    def _safe_role(self, value: Any) -> str:
        """
        目的：兼容 agent 对象差异，安全提取角色名。
        功能：优先读取 `role` 字段，缺失时返回空串。
        实现逻辑：仅做最小 `getattr()` 读取，不依赖具体 agent 类实现细节。
        可调参数：`value`。
        默认参数及原因：默认空串回退，原因是事件监听必须尽量稳，不应因个别事件结构差异失败。
        """

        if value is None:
            return ""
        return str(getattr(value, "role", ""))

    def _build_event_key(
        self,
        *,
        event_name: str,
        event: Any,
        preview: str,
        output_hash: str,
        tool_signature: str,
    ) -> str:
        """
        目的：为重复事件检测生成稳定、短小的归一化事件键。
        功能：把任务、工具、LLM 和方法事件统一映射到可比较字符串。
        实现逻辑：按事件类型优先组合最具区分度的字段，最后退化到预览哈希。
        可调参数：事件名、对象、输出哈希和工具签名。
        默认参数及原因：默认带上核心 actor 信息，原因是不同角色相同文案不应被误判成同一事件。
        """

        task_name = getattr(event, "task_name", "") or self._safe_name(getattr(event, "task", None))
        agent_role = getattr(event, "agent_role", "") or self._safe_role(getattr(event, "agent", None))
        method_name = getattr(event, "method_name", "")
        if tool_signature:
            return f"{event_name}:{tool_signature}"
        if task_name and output_hash:
            return f"{event_name}:{task_name}:{output_hash}"
        if task_name:
            return f"{event_name}:{task_name}"
        if method_name:
            return f"{event_name}:{method_name}"
        if agent_role and output_hash:
            return f"{event_name}:{agent_role}:{output_hash}"
        if agent_role:
            return f"{event_name}:{agent_role}"
        fallback_hash = _hash_text(_normalize_text(preview)) if preview else "empty"
        return f"{event_name}:{fallback_hash}"


class LoopGuardMonitor:
    """
    目的：在父进程中持续观察单个 live 子进程是否进入爆 context、重复循环或长时间无进展状态。
    功能：综合消费事件 JSONL、日志尾和产物变化信号，必要时终止当前子进程并固化证据。
    实现逻辑：轮询读取新增事件、日志和 checkpoint/manifest 心跳，再按规则优先级判断是否触发告警。
    可调参数：轮询周期、重复阈值、空转超时和 case 目录。
    默认参数及原因：默认使用保守自动截停策略，原因是本轮需求明确要求先拦失控，再汇总修复。
    """

    def __init__(self, *, spec: LiveCaseSpec, case_dir: Path, runtime_root_dir: Path) -> None:
        """
        目的：初始化单个 case 的父进程监控器。
        功能：保存 case 规格、目录、扫描状态和最近事件缓存。
        实现逻辑：只在构造时建立最小的计数器与偏移量，具体规则在轮询时实时更新。
        可调参数：`spec`、`case_dir` 和 `runtime_root_dir`。
        默认参数及原因：默认只缓存最近 50 条事件，原因是根因判断通常不需要更长上下文。
        """

        self.spec = spec
        self.case_dir = Path(case_dir).expanduser().resolve()
        self.runtime_root_dir = Path(runtime_root_dir).expanduser().resolve()
        self.monitor_dir = ensure_directory(self.case_dir / "monitor")
        self.event_log_path = self.monitor_dir / "events.jsonl"
        self.event_offset = 0
        self.log_offsets: dict[str, int] = {}
        self.file_size_cache: dict[str, Any] = {}
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=50)
        self.previous_event_key = ""
        self.previous_event_count = 0
        self.tool_repeat_counts: dict[str, int] = {}
        self.task_output_hashes: dict[str, deque[str]] = {}
        self.last_task_completion_at = time.time()
        self.last_event_at = time.time()
        self.last_progress_at = time.time()
        self.last_checkpoint_at = time.time()
        self._last_log_chunks: deque[str] = deque(maxlen=20)

    def watch_process(self, process: subprocess.Popen[str]) -> LoopAlert | None:
        """
        目的：在子进程运行期间持续执行失控监控，并在命中规则时返回告警。
        功能：循环读取新增事件、日志和产物心跳；如需截停则直接终止子进程树。
        实现逻辑：按“立即错误关键词 -> 重复循环 -> 空转超时 -> 总时长超时”的优先级顺序检查。
        可调参数：`process`。
        默认参数及原因：默认轮询到子进程退出或触发告警，原因是父进程需要掌握当前 case 的完整生命周期。
        """

        started_at = time.time()
        while process.poll() is None:
            matched_log_text = self._scan_log_growth()
            if matched_log_text:
                alert = self._build_alert(
                    rule_id="context_keyword",
                    reason=f"日志命中上下文/长度错误关键词：{matched_log_text[:200]}",
                    root_cause_category="provider_context_overflow",
                    matched_value=matched_log_text[:500],
                )
                self._abort_process(process, alert)
                return alert

            new_events = self._read_new_events()
            for entry in new_events:
                alert = self._evaluate_event_entry(entry)
                if alert is not None:
                    self._abort_process(process, alert)
                    return alert

            self._scan_artifact_progress()

            now = time.time()
            if now - max(self.last_event_at, self.last_progress_at) >= self.spec.idle_timeout_seconds:
                alert = self._build_alert(
                    rule_id="idle_timeout",
                    reason=f"连续 {self.spec.idle_timeout_seconds} 秒无事件、无日志增长、无产物变化",
                    root_cause_category="timeout_no_progress",
                    matched_value=self.spec.case_id,
                )
                self._abort_process(process, alert)
                return alert

            if now - started_at >= self.spec.timeout_seconds:
                alert = self._build_alert(
                    rule_id="case_timeout",
                    reason=f"case 总时长超过 {self.spec.timeout_seconds} 秒上限",
                    root_cause_category="timeout_no_progress",
                    matched_value=self.spec.case_id,
                )
                self._abort_process(process, alert)
                return alert

            time.sleep(EVENT_POLL_INTERVAL_SECONDS)
        return None

    def persist_abort_artifacts(self, alert: LoopAlert) -> LoopAlert:
        """
        目的：在父进程截停后把最关键的故障现场统一固化到当前 case 目录。
        功能：写出 `loop_alert.json`、`last_events.jsonl`、`log_tail.txt`、`artifact_snapshot.json` 和 `root_cause.md`。
        实现逻辑：读取最近缓存的事件和日志尾，再扫描当前运行目录中的 checkpoint、manifest 和日志文件。
        可调参数：`alert`。
        默认参数及原因：默认每次截停都全量写现场，原因是 live case 代价高，必须一次保留足够证据。
        """

        loop_alert_path = self.monitor_dir / "loop_alert.json"
        loop_alert_path.write_text(alert.model_dump_json(indent=2), encoding="utf-8")

        last_events_path = self.monitor_dir / "last_events.jsonl"
        with last_events_path.open("w", encoding="utf-8") as handle:
            for event in self.recent_events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

        log_tail_path = self.monitor_dir / "log_tail.txt"
        log_tail_path.write_text(self._build_log_tail_text(), encoding="utf-8")

        artifact_snapshot_path = self.monitor_dir / "artifact_snapshot.json"
        artifact_snapshot_path.write_text(
            json.dumps(self._build_artifact_snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        root_cause_path = self.monitor_dir / "root_cause.md"
        root_cause_path.write_text(self._build_root_cause_markdown(alert), encoding="utf-8")

        alert.evidence_files = [
            normalize_path(loop_alert_path),
            normalize_path(last_events_path),
            normalize_path(log_tail_path),
            normalize_path(artifact_snapshot_path),
            normalize_path(root_cause_path),
        ]
        loop_alert_path.write_text(alert.model_dump_json(indent=2), encoding="utf-8")
        return alert

    def _read_new_events(self) -> list[dict[str, Any]]:
        """
        目的：读取事件 JSONL 自上次轮询后的新增片段。
        功能：返回可继续参与规则判断的新事件列表，并刷新事件文件偏移量。
        实现逻辑：按字节偏移增量读取，再逐行 JSON 解析；损坏行会被安全跳过。
        可调参数：当前无显式参数。
        默认参数及原因：默认允许个别坏行被跳过，原因是监控逻辑不能因为单条事件损坏而失效。
        """

        if not self.event_log_path.exists():
            return []
        with self.event_log_path.open("r", encoding="utf-8") as handle:
            handle.seek(self.event_offset)
            raw_text = handle.read()
            self.event_offset = handle.tell()
        events: list[dict[str, Any]] = []
        for line in raw_text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            self.recent_events.append(event)
            self.last_event_at = time.time()
            self.last_progress_at = max(self.last_progress_at, self.last_event_at)
        return events

    def _scan_log_growth(self) -> str:
        """
        目的：扫描运行目录内日志文件的新增长文本，并做错误关键词检测。
        功能：实时发现 context/长度类错误，同时缓存最近日志尾供截停后落盘。
        实现逻辑：按文件偏移量增量读取 `*.txt` 和 `*.json` 日志，逐块匹配关键字。
        可调参数：当前无显式参数。
        默认参数及原因：默认递归扫描运行目录，原因是 run slug 在 prepare 阶段前并不固定可知。
        """

        if not self.runtime_root_dir.exists():
            return ""
        for log_path in sorted(self.runtime_root_dir.rglob("*")):
            if not log_path.is_file():
                continue
            if log_path.suffix.lower() not in {".txt", ".json", ".jsonl", ".md"}:
                continue
            normalized_path = normalize_path(log_path)
            current_size = log_path.stat().st_size
            previous_size = self.log_offsets.get(normalized_path, 0)
            if current_size <= previous_size:
                continue
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(previous_size)
                chunk = handle.read()
            self.log_offsets[normalized_path] = current_size
            self.last_progress_at = time.time()
            if chunk:
                self._last_log_chunks.append(f"===== {normalized_path} =====\n{chunk}")
                normalized_chunk = _normalize_text(chunk)
                if any(keyword in normalized_chunk for keyword in CONTEXT_ERROR_KEYWORDS):
                    return chunk
        return ""

    def _scan_artifact_progress(self) -> None:
        """
        目的：检测 checkpoint、manifest 和日志文件是否持续发生外部可见变化。
        功能：为“无进展超时”提供独立于事件流的心跳信号。
        实现逻辑：比较关键文件的大小缓存；checkpoint 数量增加时额外刷新 `last_checkpoint_at`。
        可调参数：当前无显式参数。
        默认参数及原因：默认只按文件大小判断，原因是这比全量内容比较更轻量且足够稳定。
        """

        if not self.runtime_root_dir.exists():
            return

        changed = False
        checkpoint_paths = list(self.runtime_root_dir.rglob("checkpoints/*.json"))
        if checkpoint_paths:
            current_checkpoint_key = "|".join(sorted(normalize_path(path) for path in checkpoint_paths))
            previous_checkpoint_key = self.file_size_cache.get("__checkpoint_key__", "")
            if current_checkpoint_key != previous_checkpoint_key:
                self.file_size_cache["__checkpoint_key__"] = current_checkpoint_key
                self.last_checkpoint_at = time.time()
                changed = True

        for pattern in ("**/run_manifest.json", "**/logs/*.txt", "**/logs/*.json"):
            for path in self.runtime_root_dir.glob(pattern):
                if not path.is_file():
                    continue
                normalized_path = normalize_path(path)
                current_size = path.stat().st_size
                previous_size = self.file_size_cache.get(normalized_path)
                if previous_size != current_size:
                    self.file_size_cache[normalized_path] = current_size
                    changed = True

        if changed:
            self.last_progress_at = time.time()

    def _evaluate_event_entry(self, entry: dict[str, Any]) -> LoopAlert | None:
        """
        目的：对单条新事件执行关键词、重复工具和重复输出等规则判断。
        功能：命中规则时返回 `LoopAlert`，否则返回空值。
        实现逻辑：先做立即错误关键词检测，再依次更新重复计数器和任务完成心跳。
        可调参数：`entry`。
        默认参数及原因：默认一条事件只触发一个最高优先级告警，原因是父进程一旦截停就无需继续累积更多告警。
        """

        preview = str(entry.get("preview", ""))
        normalized_preview = _normalize_text(preview)
        if any(keyword in normalized_preview for keyword in CONTEXT_ERROR_KEYWORDS):
            return self._build_alert(
                rule_id="context_keyword",
                reason="事件预览命中上下文/长度错误关键词",
                root_cause_category="provider_context_overflow",
                matched_value=preview[:500],
            )

        if any(keyword in normalized_preview for keyword in RATE_LIMIT_KEYWORDS):
            return self._build_alert(
                rule_id="rate_limit_keyword",
                reason="事件预览命中限流关键词",
                root_cause_category="provider_rate_limit",
                matched_value=preview[:500],
            )

        event_key = str(entry.get("event_key", ""))
        if event_key and event_key == self.previous_event_key:
            self.previous_event_count += 1
        else:
            self.previous_event_key = event_key
            self.previous_event_count = 1

        if (
            self.previous_event_count >= CONSECUTIVE_REPEAT_THRESHOLD
            and time.time() - self.last_checkpoint_at >= CHECKPOINT_STALL_SECONDS
            and entry.get("event_name")
            in {
                "ToolUsageStartedEvent",
                "ToolUsageFinishedEvent",
                "LLMCallStartedEvent",
                "LLMCallCompletedEvent",
            }
        ):
            return self._build_alert(
                rule_id="repeated_event_key",
                reason="同一归一化事件键连续重复且长时间无新 checkpoint",
                root_cause_category="prompt_loop",
                matched_value=event_key,
            )

        if entry.get("event_name") == "TaskCompletedEvent":
            self.last_task_completion_at = time.time()
            self.tool_repeat_counts.clear()
            task_name = str(entry.get("task_name", ""))
            output_hash = str(entry.get("output_hash", ""))
            if task_name and output_hash:
                task_hashes = self.task_output_hashes.setdefault(task_name, deque(maxlen=3))
                task_hashes.append(output_hash)
                if len(task_hashes) >= TASK_OUTPUT_REPEAT_THRESHOLD and len(set(task_hashes)) == 1:
                    return self._build_alert(
                        rule_id="repeated_task_output",
                        reason="同一任务输出哈希重复出现 3 次",
                        root_cause_category="prompt_loop",
                        matched_value=f"{task_name}:{output_hash}",
                    )

        if entry.get("event_name") == "TaskFailedEvent":
            self.tool_repeat_counts.clear()

        if entry.get("event_name") == "ToolUsageStartedEvent":
            tool_signature = str(entry.get("tool_signature", ""))
            if tool_signature:
                self.tool_repeat_counts[tool_signature] = self.tool_repeat_counts.get(tool_signature, 0) + 1
                if (
                    self.tool_repeat_counts[tool_signature] >= TOOL_REPEAT_THRESHOLD
                    and time.time() - self.last_task_completion_at >= EVENT_POLL_INTERVAL_SECONDS
                ):
                    return self._build_alert(
                        rule_id="repeated_tool_signature",
                        reason="同一工具签名重复调用 3 次且期间没有任务完成",
                        root_cause_category="tool_loop",
                        matched_value=tool_signature,
                    )
        return None

    def _abort_process(self, process: subprocess.Popen[str], alert: LoopAlert) -> None:
        """
        目的：在命中规则后可靠终止当前子进程及其子树，避免继续消耗 token。
        功能：调用 Windows `taskkill /T /F`，并在失败时回退到普通 `kill()`。
        实现逻辑：优先杀整棵进程树，再等待短暂时间确保系统资源回收。
        可调参数：`process` 和 `alert`。
        默认参数及原因：默认强制终止，原因是失控 case 的首要目标是止损而不是温和退出。
        """

        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.wait(timeout=10)
            except Exception:
                pass
        self.persist_abort_artifacts(alert)

    def _build_alert(
        self,
        *,
        rule_id: str,
        reason: str,
        root_cause_category: RootCauseCategory,
        matched_value: str,
    ) -> LoopAlert:
        """
        目的：统一创建当前 case 的 `LoopAlert` 对象。
        功能：把触发规则、原因、根因分类和命中值收口成固定结构。
        实现逻辑：直接使用当前时间与规则入参构造模型实例。
        可调参数：规则编号、原因、根因分类和命中值。
        默认参数及原因：默认不在此处直接写文件，原因是父进程需要先完成进程终止再固化证据。
        """

        return LoopAlert(
            rule_id=rule_id,
            reason=reason,
            root_cause_category=root_cause_category,
            matched_value=matched_value,
        )

    def _build_log_tail_text(self) -> str:
        """
        目的：生成截停后可直接人工查看的日志尾文本。
        功能：拼接最近读取到的日志增量，并在必要时补充当前关键日志文件的尾部内容。
        实现逻辑：优先使用运行中缓存的日志块，再回退读取 manifest/flow/console 文件尾部。
        可调参数：当前无显式参数。
        默认参数及原因：默认限制总字符数，原因是现场文件要够用但不能膨胀到难以查看。
        """

        combined = "\n".join(self._last_log_chunks)
        if combined:
            return combined[-LOG_TAIL_CHAR_LIMIT:]

        fallback_chunks: list[str] = []
        for path in self.runtime_root_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".txt", ".json", ".jsonl"}:
                content = read_text_if_exists(path)
                if content:
                    fallback_chunks.append(f"===== {normalize_path(path)} =====\n{content[-4000:]}")
        return "\n".join(fallback_chunks)[-LOG_TAIL_CHAR_LIMIT:]

    def _build_artifact_snapshot(self) -> dict[str, Any]:
        """
        目的：生成当前运行目录的最小产物快照，便于修复 backlog 直接引用。
        功能：列出 manifest、checkpoint、日志和近期事件的主要路径与大小信息。
        实现逻辑：递归扫描运行目录并按文件类型分类，输出稳定字典。
        可调参数：当前无显式参数。
        默认参数及原因：默认只保留文件路径和大小，原因是第一版快照主要用于定位而不是内容归档。
        """

        checkpoints = [
            {"path": normalize_path(path), "size": path.stat().st_size}
            for path in sorted(self.runtime_root_dir.rglob("checkpoints/*.json"))
            if path.is_file()
        ]
        manifests = [
            {"path": normalize_path(path), "size": path.stat().st_size}
            for path in sorted(self.runtime_root_dir.rglob("run_manifest.json"))
            if path.is_file()
        ]
        logs = [
            {"path": normalize_path(path), "size": path.stat().st_size}
            for path in sorted(self.runtime_root_dir.rglob("logs/*"))
            if path.is_file()
        ]
        return {
            "generated_at": utc_timestamp(),
            "runtime_root_dir": normalize_path(self.runtime_root_dir),
            "manifests": manifests,
            "checkpoints": checkpoints,
            "logs": logs,
            "recent_events_count": len(self.recent_events),
        }

    def _build_root_cause_markdown(self, alert: LoopAlert) -> str:
        """
        目的：生成可直接被人工阅读和 backlog 引用的根因摘要 Markdown。
        功能：输出 case、规则、根因分类、触发原因和证据位置。
        实现逻辑：使用固定模板拼装最小诊断说明。
        可调参数：`alert`。
        默认参数及原因：默认保持结构稳定，原因是汇总阶段需要按固定模式引用这份文件。
        """

        return "\n".join(
            [
                f"# {self.spec.case_id} Root Cause",
                "",
                f"- 触发时间：{alert.triggered_at}",
                f"- 触发规则：{alert.rule_id}",
                f"- 根因分类：{alert.root_cause_category}",
                f"- 触发原因：{alert.reason}",
                f"- 命中值：{alert.matched_value or '无'}",
                f"- 证据目录：{normalize_path(self.monitor_dir)}",
            ]
        ).strip() + "\n"
