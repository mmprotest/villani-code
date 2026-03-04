from __future__ import annotations

import asyncio
from pathlib import Path

from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit

from ui.command_palette import CommandAction, CommandPalette
from ui.diff_viewer import DiffViewer
from ui.settings import SettingsManager
from ui.status_bar import StatusBar
from ui.task_board import TaskManager
from ui.themes import get_theme
from villani_code.state import Runner
from villani_code.tui.app import InputBar, TUIApp, TranscriptView
from villani_code.tui.controller import Controller
from villani_code.tui.keybindings import build_keybindings
from villani_code.tui.modals.help import build_help_modal
from villani_code.tui.modals.output_viewer import OutputViewerModal
from villani_code.tui.modals.palette import PaletteModal
from villani_code.tui.modals.settings import SettingsModal
from villani_code.tui.panels.diff_viewer_panel import DiffViewerPanel
from villani_code.tui.panels.task_board_panel import TaskBoardPanel
from villani_code.tui.state import ActiveModal, UIState


class InteractiveShell:
    def __init__(self, runner: Runner, repo: Path):
        self.runner = runner
        self.repo = repo
        self.palette = CommandPalette()
        self.task_manager = TaskManager()
        self.status_bar = StatusBar()
        self.diff_viewer = DiffViewer(repo)
        self.settings = SettingsManager(repo)
        self.applied_settings = self.settings.load()
        self.theme = get_theme(self.applied_settings.theme)

    def run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        state = UIState(verbose_tool_output=self.applied_settings.verbose)
        skills = list(self.runner.skills.keys())
        completions = [i.trigger for i in self.palette.items] + ["/help", "/tasks", "/diff", "/settings", "/rewind", "/export", "/fork", "/exit"] + skills
        input_bar = InputBar(FuzzyWordCompleter(completions, WORD=True))
        transcript = TranscriptView()
        controller = Controller(self.runner, self.repo, state, transcript, self.task_manager)
        def _on_stream(event: dict) -> None:
            if event.get("type") != "content_block_delta":
                return
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                transcript.append_assistant_delta(delta.get("text", ""))

        def _on_tool_event(kind: str, payload: dict) -> None:
            if kind == "tool_use":
                transcript.append_tool_call(payload.get("name", ""), payload.get("input", {}))
            elif kind == "tool_result":
                full = str(payload.get("result", {}).get("content", ""))
                out_id = payload.get("id", "")
                transcript.store_full_output(out_id, full)
                transcript.append_tool_result(payload.get("name", ""), full[:240], out_id)

        self.runner.interactive_mode = True
        self.runner.stream_event_handler = lambda event: _on_stream(event)
        self.runner.tool_event_handler = lambda kind, payload: _on_tool_event(kind, payload)
        task_panel = TaskBoardPanel(self.task_manager)
        diff_panel = DiffViewerPanel(self.repo)
        diff_panel.load()

        async def _run_action(action: CommandAction) -> None:
            await controller.run_action(action)
            state.active_modal = ActiveModal.NONE

        palette_modal = PaletteModal(self.palette, lambda action: asyncio.create_task(_run_action(action)))
        settings_modal = SettingsModal(self.applied_settings, lambda verbose, _auto: setattr(state, "verbose_tool_output", verbose))
        help_modal = build_help_modal()
        output_modal = OutputViewerModal()

        def _schedule(name: str) -> None:
            if name == "checkpoint":
                self.runner.checkpoints.create([], message_index=0)

        def _open_output() -> None:
            state.active_modal = ActiveModal.OUTPUT
            output_modal.set_text(transcript.get_full_output(state.output_view_id))

        kb = build_keybindings(state, _schedule, _open_output, transcript.toggle_selected_fold)

        async def _accept(buff) -> bool:
            value = input_bar.textarea.text
            input_bar.textarea.buffer.reset()
            if value.strip() == "/exit":
                app.app.exit()
                return False
            await controller.handle_input(value)
            app.invalidate()
            return False

        input_bar.textarea.accept_handler = _accept

        @Condition
        def show_tasks() -> bool:
            return state.show_tasks

        @Condition
        def show_diff() -> bool:
            return state.show_diff

        right_panel = HSplit([
            ConditionalContainer(task_panel.container, filter=show_tasks),
            ConditionalContainer(diff_panel.container, filter=show_diff),
        ])
        bottom_panel = right_panel
        app = TUIApp(
            state=state,
            transcript=transcript,
            input_bar=input_bar,
            status_bar=self.status_bar,
            key_bindings=kb,
            right_panel=right_panel,
            bottom_panel=bottom_panel,
            palette_modal=palette_modal.container,
            help_modal=help_modal,
            settings_modal=settings_modal.container,
            output_modal=output_modal.container,
            style=self.theme.prompt_toolkit_style,
        )
        await app.run()
