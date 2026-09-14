"""Desktop panel for the local-first Anharmonic specialist swarm."""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..agent_harness import AgentHarness, IDENTITY_BY_ID, IDENTITIES, ProviderConfig


class AgentHarnessPanel(QWidget):
    """A visible queue of bounded, review-only specialist jobs."""

    def __init__(self, harness: AgentHarness, parent=None):
        super().__init__(parent)
        self.harness = harness
        self.context_summary = ""
        self._thread: threading.Thread | None = None
        self.setObjectName("agentHarness")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        heading = QHBoxLayout()
        title = QLabel("AGENTIC MPC LABS · SWARM HARNESS")
        title.setObjectName("workspaceTitle")
        heading.addWidget(title)
        heading.addStretch()
        self.connection = QLabel("LOCAL-FIRST · no project data leaves the studio automatically")
        self.connection.setObjectName("studioFlow")
        heading.addWidget(self.connection)
        layout.addLayout(heading)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.roster = QTableWidget(len(IDENTITIES), 2)
        self.roster.setHorizontalHeaderLabels(["SPECIALIST", "MICRO-BATCH FOCUS"])
        self.roster.verticalHeader().hide()
        self.roster.setSelectionBehavior(QTableWidget.SelectRows)
        self.roster.setSelectionMode(QTableWidget.SingleSelection)
        self.roster.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, identity in enumerate(IDENTITIES):
            name = QTableWidgetItem(identity.name)
            name.setData(Qt.UserRole, identity.id)
            self.roster.setItem(row, 0, name)
            self.roster.setItem(row, 1, QTableWidgetItem(identity.focus))
        self.roster.resizeColumnsToContents()
        self.roster.selectRow(0)
        splitter.addWidget(self.roster)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.task = QLineEdit()
        self.task.setPlaceholderText(
            "Describe one small review batch, e.g. ‘audit audio clip edge stretching’"
        )
        self.task.setText("Review the selected workflow for friction and safe improvements")
        center_layout.addWidget(self.task)
        controls = QHBoxLayout()
        self.queue_button = QPushButton("QUEUE MICRO-BATCH")
        self.queue_button.setObjectName("go")
        self.queue_button.clicked.connect(self.queue_selected)
        controls.addWidget(self.queue_button)
        self.run_button = QPushButton("RUN SELECTED")
        self.run_button.clicked.connect(self.run_selected)
        controls.addWidget(self.run_button)
        self.clear_button = QPushButton("CLEAR COMPLETE")
        self.clear_button.clicked.connect(self.clear_completed)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        center_layout.addLayout(controls)
        self.jobs = QTableWidget(0, 3)
        self.jobs.setHorizontalHeaderLabels(["AGENT", "STATE", "TASK"])
        self.jobs.verticalHeader().hide()
        self.jobs.setSelectionBehavior(QTableWidget.SelectRows)
        self.jobs.setSelectionMode(QTableWidget.SingleSelection)
        self.jobs.setEditTriggers(QTableWidget.NoEditTriggers)
        self.jobs.itemSelectionChanged.connect(self._show_selected_result)
        center_layout.addWidget(self.jobs, 1)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText(
            "Results are proposals only. Nothing is applied automatically."
        )
        self.result.setMinimumHeight(130)
        center_layout.addWidget(self.result)
        splitter.addWidget(center)

        config = QWidget()
        form = QFormLayout(config)
        self.provider = QComboBox()
        self.provider.addItem("Offline brief (no network)", "offline")
        self.provider.addItem("Free Cloud · Ollama gpt-oss 120B", "ollama")
        self.provider.addItem("Local Fast · Qwen2.5 Coder 0.5B", "local-fast")
        self.provider.addItem("Local 9B · Qwen3.5 Q4 (requires idle RAM)", "local-9b")
        self.provider.addItem("OpenAI Responses API", "openai")
        self.provider.addItem("Anthropic Messages API", "anthropic")
        self.provider.addItem("OpenAI-compatible local model", "openai-compatible")
        self.model = QLineEdit()
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("Default provider endpoint")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("PROVIDER", self.provider)
        form.addRow("MODEL", self.model)
        form.addRow("ENDPOINT", self.endpoint)
        form.addRow(
            QLabel(
                "Credentials are read only at Run time from OPENAI_API_KEY, ANTHROPIC_API_KEY, or ANHARMONIC_LOCAL_API_KEY. They are never shown or saved."
            )
        )
        self.save_button = QPushButton("SAVE NON-SECRET SETTINGS")
        self.save_button.clicked.connect(self.save_settings)
        form.addRow(self.save_button)
        guardrail = QLabel(
            "GUARDRAILS\n• Ten named specialists, one bounded task at a time\n"
            "• No shell, filesystem, audio-engine, or auto-apply access\n"
            "• Remote calls occur only when you press Run selected\n"
            "• Results are review notes for you to approve"
        )
        guardrail.setWordWrap(True)
        form.addRow(guardrail)
        splitter.addWidget(config)
        splitter.setSizes([280, 520, 300])
        layout.addWidget(splitter, 1)

        self._load_config()
        self._refresh_jobs()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_jobs)
        self.refresh_timer.start(300)

    def set_context_summary(self, summary: str) -> None:
        self.context_summary = summary[:2000]

    def _load_config(self) -> None:
        config = self.harness.config
        index = self.provider.findData(config.provider)
        self.provider.setCurrentIndex(max(0, index))
        self.model.setText(config.model)
        self.endpoint.setText(config.endpoint)
        self._provider_changed()

    def _provider_changed(self, *_args) -> None:
        selected = self.provider.currentData()
        remote = selected not in {"offline", "local-fast", "local-9b", "ollama"}
        self.endpoint.setEnabled(remote)
        if selected == "ollama":
            self.model.setText("gpt-oss:120b-cloud")
            self.connection.setText(
                "FREE CLOUD · sign in to Ollama; calls route through local Ollama only"
            )
        elif selected == "local-9b":
            self.connection.setText("LOCAL 9B · guarded until at least 8 GiB RAM is free")
        elif selected == "local-fast":
            self.connection.setText(
                "LOCAL FAST · runs directly through the installed NEUDO runtime"
            )
        else:
            self.connection.setText(
                "REMOTE IS EXPLICIT · credentials remain environment-only"
                if remote
                else "LOCAL-FIRST · no project data leaves the studio automatically"
            )

    def _config_from_controls(self) -> ProviderConfig:
        return ProviderConfig(
            str(self.provider.currentData()),
            self.model.text().strip() or "local-review",
            self.endpoint.text().strip(),
        )

    def save_settings(self) -> None:
        try:
            self.harness.config = self._config_from_controls()
            self.harness.save()
            self.connection.setText("SETTINGS SAVED · credentials were not stored")
        except ValueError as exc:
            self.connection.setText(f"SETTINGS NOT SAVED · {exc}")

    def _selected_agent_id(self) -> str:
        row = self.roster.currentRow()
        item = self.roster.item(max(0, row), 0)
        return str(item.data(Qt.UserRole))

    def queue_selected(self) -> None:
        task = self.task.text().strip()
        try:
            job = self.harness.queue(self._selected_agent_id(), task, self.context_summary)
        except ValueError as exc:
            self.connection.setText(f"NOT QUEUED · {exc}")
            return
        self._refresh_jobs(select_id=job.id)
        self.connection.setText(f"QUEUED · {IDENTITY_BY_ID[job.agent_id].name} · review only")

    def _selected_job(self):
        row = self.jobs.currentRow()
        if row < 0 or row >= len(self.harness.jobs):
            return None
        job_id = self.jobs.item(row, 0).data(Qt.UserRole)
        return next((job for job in self.harness.jobs if job.id == job_id), None)

    def run_selected(self) -> None:
        job = self._selected_job()
        if job is None:
            self.connection.setText("SELECT A QUEUED MICRO-BATCH FIRST")
            return
        try:
            self.harness.config = self._config_from_controls()
        except ValueError as exc:
            self.connection.setText(f"CANNOT RUN · {exc}")
            return
        if self.harness.config.provider == "offline":
            self.harness.run(job)
            self._refresh_jobs(select_id=job.id)
            return
        if self._thread is not None and self._thread.is_alive():
            self.connection.setText("A REMOTE REVIEW IS ALREADY RUNNING")
            return
        self._thread = threading.Thread(target=self.harness.run, args=(job,), daemon=True)
        self._thread.start()
        self.connection.setText("REMOTE REVIEW RUNNING · you can keep working")
        self._refresh_jobs(select_id=job.id)

    def clear_completed(self) -> None:
        self.harness.jobs = [job for job in self.harness.jobs if job.state != "complete"]
        self.result.clear()
        self._refresh_jobs()

    def _refresh_jobs(self, *_args, select_id: str | None = None) -> None:
        previous = select_id
        current = self._selected_job()
        if previous is None and current is not None:
            previous = current.id
        self.jobs.setRowCount(len(self.harness.jobs))
        for row, job in enumerate(self.harness.jobs):
            who = QTableWidgetItem(IDENTITY_BY_ID[job.agent_id].name)
            who.setData(Qt.UserRole, job.id)
            self.jobs.setItem(row, 0, who)
            self.jobs.setItem(row, 1, QTableWidgetItem(job.state.upper()))
            self.jobs.setItem(row, 2, QTableWidgetItem(job.task))
            if job.id == previous:
                self.jobs.selectRow(row)
        self.jobs.resizeColumnsToContents()
        self._show_selected_result()

    def _show_selected_result(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        self.result.setPlainText(job.result or job.error or "Queued · no result yet")
