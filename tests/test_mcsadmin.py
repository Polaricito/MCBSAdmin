"""Unit tests for MCBSAdmin (no network required)."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcbsadmin.config import Config, default_config_dir
from mcbsadmin.stats import SystemStats
from mcbsadmin.util import LogBuffer, clamp, fmt_bytes, fmt_seconds, offline_uuid, truncate
from mcbsadmin import versions


class TestUtil(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-3, 0, 10), 0)
        self.assertEqual(clamp(99, 0, 10), 10)

    def test_fmt_bytes(self):
        self.assertEqual(fmt_bytes(1024), "1 KiB")
        self.assertEqual(fmt_bytes(1024 * 1024 * 1024), "1 GiB")
        self.assertEqual(fmt_bytes(1536), "1.5 KiB")
        self.assertEqual(fmt_bytes(0), "0 B")

    def test_fmt_seconds(self):
        self.assertEqual(fmt_seconds(90), "01:30")
        self.assertEqual(fmt_seconds(3600), "01:00:00")

    def test_truncate(self):
        self.assertEqual(truncate("hello", 10), "hello")
        self.assertEqual(len(truncate("hello world", 5)), 5)

    def test_offline_uuid(self):
        self.assertEqual(
            offline_uuid("Steve"), "5627dd98-e6be-3c21-b8a8-e92344183641"
        )


class TestLogBuffer(unittest.TestCase):
    def test_bounded(self):
        buf = LogBuffer(maxlen=3)
        for i in range(10):
            buf.append(f"line{i}")
        self.assertEqual(buf.tail(10), ["line7", "line8", "line9"])


class TestBedrockPlayerLog(unittest.TestCase):
    def test_connected_and_disconnected(self):
        from mcbsadmin.server import ServerManager

        with tempfile.TemporaryDirectory() as d:
            s = ServerManager(Config(os.path.join(d, "c.json")), LogBuffer())
            s._detect_player_events(
                "[2026-08-13 15:04:05:123 INFO] "
                "Player connected: Steve, xuid: 2535452973466207"
            )
            self.assertIn("Steve", s.players)
            # "Player Spawned" also marks join
            s._detect_player_events(
                "[2026-08-13 15:04:06:456 INFO] "
                "Player Spawned: Notch, xuid: 2535452973456789"
            )
            self.assertIn("Notch", s.players)
            s._detect_player_events(
                "[2026-08-13 15:30:00:000 INFO] "
                "Player disconnected: Steve, xuid: 2535452973466207"
            )
            self.assertNotIn("Steve", s.players)
            self.assertIn("Notch", s.players)

    def test_names_with_spaces(self):
        from mcbsadmin.server import ServerManager

        with tempfile.TemporaryDirectory() as d:
            s = ServerManager(Config(os.path.join(d, "c.json")), LogBuffer())
            s._detect_player_events(
                "[2026-08-13 15:04:05:123 INFO] "
                "Player connected: Steve Smith, xuid: 2535452973466207"
            )
            self.assertIn("Steve Smith", s.players)
            s._detect_player_events(
                "[2026-08-13 15:30:00:000 INFO] "
                "Player disconnected: Steve Smith, xuid: 2535452973466207"
            )
            self.assertNotIn("Steve Smith", s.players)


class TestServerStartedDetection(unittest.TestCase):
    def test_status_turns_running_on_server_started(self):
        from mcbsadmin.server import ServerManager

        with tempfile.TemporaryDirectory() as d:
            s = ServerManager(Config(os.path.join(d, "c.json")), LogBuffer())
            s.status = "starting"
            s._handle_line("[2026-08-13 15:04:05:123 INFO] Server started.")
            self.assertEqual(s.status, "running")


class TestBedrockProperties(unittest.TestCase):
    def _props(self, mgr, path):
        from mcbsadmin.server import read_properties

        mgr._write_properties(path)
        return read_properties(path)

    def test_defaults_are_bedrock(self):
        from mcbsadmin.config import Config
        from mcbsadmin.server import ServerManager
        from mcbsadmin.util import LogBuffer

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(os.path.join(d, "c.json"))
            mgr = ServerManager(cfg, LogBuffer())
            path = os.path.join(d, "server.properties")
            props = self._props(mgr, path)
            self.assertEqual(props["server-port"], "19132")
            self.assertEqual(props["server-portv6"], "19133")
            self.assertEqual(props["server-name"], cfg.get("motd"))
            self.assertEqual(props["max-players"], "10")
            # no Java-era properties leaked through
            self.assertNotIn("use-native-transport", props)
            self.assertNotIn("enable-rcon", props)

    def test_gameport_from_config(self):
        from mcbsadmin.config import Config
        from mcbsadmin.server import ServerManager
        from mcbsadmin.util import LogBuffer

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(os.path.join(d, "c.json"))
            cfg.set("gameport", 19165)
            cfg.set("gameportv6", 19166)
            mgr = ServerManager(cfg, LogBuffer())
            path = os.path.join(d, "server.properties")
            props = self._props(mgr, path)
            self.assertEqual(props["server-port"], "19165")
            self.assertEqual(props["server-portv6"], "19166")

    def test_world_options_written(self):
        from mcbsadmin.config import Config
        from mcbsadmin.server import ServerManager
        from mcbsadmin.util import LogBuffer

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(os.path.join(d, "c.json"))
            cfg.set("world", {"difficulty": "hard", "pvp": "false",
                              "allow-list": "true"})
            mgr = ServerManager(cfg, LogBuffer())
            path = os.path.join(d, "server.properties")
            props = self._props(mgr, path)
            self.assertEqual(props.get("difficulty"), "hard")
            self.assertEqual(props.get("pvp"), "false")
            self.assertEqual(props.get("allow-list"), "true")


class TestFieldModal(unittest.TestCase):
    """World Options modal editing logic (no curses needed)."""

    def _app(self, tmpdir):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        app.config = Config(os.path.join(tmpdir, "c.json"))
        app.log = LogBuffer()
        app.message = ""
        app._message_at = 0.0
        app.modal = None
        app.prev_modal = None
        app._dirty = set()
        app.server = type(
            "S",
            (),
            {"proc": None, "pid": None, "players": set(), "player_ips": {}},
        )()
        app._dirty = set()
        return app

    def test_save_world_options_serializes_and_drops_defaults(self):
        from mcbsadmin.tui import FieldModal, WORLD_FIELDS

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            m = FieldModal("t", WORLD_FIELDS,
                           {"difficulty": "hard", "pvp": False,
                            "view-distance": None}, None)
            app._save_world_options(m.values)
            self.assertEqual(app.config.get("world"),
                             {"difficulty": "hard", "pvp": "false"})

    def test_world_options_persist_through_setup_files(self):
        # full path: open World Options from settings, edit, hit done, and
        # verify config + server.properties both reflect the new values
        from mcbsadmin.server import ServerManager, read_properties
        from mcbsadmin.tui import SettingsModal, WORLD_FIELDS
        from mcbsadmin.util import LogBuffer

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            app.server = ServerManager(app.config, LogBuffer())
            app.modal = SettingsModal(app.config, d)
            app._open_world_modal()
            m = app.modal
            m.values["difficulty"] = "hard"
            m.values["max-players"] = 20
            m.sel = len(WORLD_FIELDS)
            app._handle_modal_key(10)  # done -> save + back to settings
            self.assertIsInstance(app.modal, SettingsModal)
            props = read_properties(os.path.join(d, "server.properties"))
            self.assertEqual(props.get("difficulty"), "hard")
            self.assertEqual(props.get("max-players"), "20")

    def test_open_world_modal_seeds_from_properties(self):
        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            prop = os.path.join(d, "server.properties")
            with open(prop, "w") as fh:
                fh.write("difficulty=normal\nview-distance=12\npvp=false\n")
            app.config.set("world", {"gamemode": "spectator"})
            app._open_world_modal()
            m = app.modal
            self.assertEqual(m.values["difficulty"], "normal")
            self.assertEqual(m.values["view-distance"], 12)
            self.assertIs(m.values["pvp"], False)
            self.assertEqual(m.values["gamemode"], "spectator")
            self.assertIsNone(m.values["max-players"])

    def test_enter_on_done_saves_and_closes(self):
        from mcbsadmin.tui import SettingsModal, WORLD_FIELDS

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            app.modal = SettingsModal(app.config, d)
            app._open_world_modal()  # sets prev_modal = settings
            m = app.modal
            m.values["difficulty"] = "easy"
            m.sel = len(WORLD_FIELDS)
            app.modal = m
            app._handle_modal_key(10)  # Enter on "done"
            # returns to the settings screen (sub-menu pops back)
            self.assertIsInstance(app.modal, SettingsModal)
            self.assertEqual(app.config.get("world")["difficulty"], "easy")

    def test_esc_from_world_submenu_returns_to_settings(self):
        from mcbsadmin.tui import FieldModal, SettingsModal

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            app.modal = SettingsModal(app.config, d)
            app._open_world_modal()
            self.assertIsInstance(app.modal, FieldModal)
            app._handle_modal_key(27)  # ESC
            self.assertIsInstance(app.modal, SettingsModal)

    def test_cycle_bool_and_choice(self):
        from mcbsadmin.tui import FieldModal, WORLD_FIELDS

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            f = FieldModal("t", WORLD_FIELDS,
                           {"pvp": None, "difficulty": None}, None)
            di = next(i for i, e in enumerate(WORLD_FIELDS) if e[0] == "difficulty")
            app._field_enter(f, di)
            self.assertEqual(f.values["difficulty"], "peaceful")
            app._field_enter(f, di)
            self.assertEqual(f.values["difficulty"], "easy")
            pi = next(i for i, e in enumerate(WORLD_FIELDS) if e[0] == "pvp")
            app._field_enter(f, pi)
            self.assertIs(f.values["pvp"], True)
            app._field_enter(f, pi)
            self.assertIs(f.values["pvp"], False)

    def test_int_editing_commits_and_blank_is_auto(self):
        from mcbsadmin.tui import FieldModal, WORLD_FIELDS

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            f = FieldModal("t", WORLD_FIELDS, {"max-players": None}, None)
            mi = next(i for i, e in enumerate(WORLD_FIELDS) if e[0] == "max-players")
            f.sel = mi
            app._field_enter(f, mi)
            for ch_ in "42":
                app._handle_field_key(f, ord(ch_))
            self.assertEqual(f.edit_buf, "42")
            app._commit_field(f)
            self.assertEqual(f.values["max-players"], 42)
            self.assertIsNone(f.editing)
            # empty buffer -> None (auto)
            app._field_enter(f, mi)
            for _ in range(len(f.edit_buf)):
                app._handle_field_key(f, 263)  # backspace all digits
            app._commit_field(f)
            self.assertIsNone(f.values["max-players"])

    def test_settings_row_tokens(self):
        from mcbsadmin.tui import SettingsModal

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            m = SettingsModal(app.config, d)
            m.sel = 0
            text, token = app._settings_row(m, 0, 40)
            self.assertEqual(token, "settings:name")
            self.assertIn("name:", text)
            self.assertNotIn("desc:", text)
            m.sel = 1
            text, token = app._settings_row(m, 1, 40)
            self.assertEqual(token, "settings:port")
            self.assertIn("port: 19132", text)  # Bedrock default port
            m.sel = 2
            text, token = app._settings_row(m, 2, 40)
            self.assertEqual(token, "settings:done")
            self.assertIn("save", text)
            # Java-era settings are gone from the Bedrock build
            self.assertNotIn("jvm", " ".join(m.actions))
            self.assertNotIn("icon", " ".join(m.actions))
            self.assertNotIn("world options", " ".join(m.actions))
            self.assertEqual(m.actions, ["name", "port", "done"])

    def test_settings_port_editing_and_save(self):
        from mcbsadmin.server import ServerManager, read_properties
        from mcbsadmin.tui import SettingsModal
        from mcbsadmin.util import LogBuffer

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            app.server = ServerManager(app.config, LogBuffer())
            m = SettingsModal(app.config, d)
            app.modal = m
            m.sel = 1
            app._settings_start_edit(m)
            self.assertEqual(m.editing, "port")
            self.assertEqual(m.edit_buf, "19132")
            for _ in range(5):
                app._handle_modal_key(263)  # backspace
            for c in "22222":
                app._handle_modal_key(ord(c))
            app._handle_modal_key(10)  # Enter commits
            self.assertIsNone(m.editing)
            self.assertEqual(m.port, 22222)
            m.sel = 2  # move to the done row
            app._handle_modal_key(10)  # Enter on save/done
            self.assertIsNone(app.modal)
            self.assertEqual(app.config.get("gameport"), 22222)
            props = read_properties(os.path.join(d, "server.properties"))
            self.assertEqual(props.get("server-port"), "22222")

    def test_settings_port_keeps_default_when_empty(self):
        from mcbsadmin.tui import SettingsModal

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            m = SettingsModal(app.config, d)
            app.modal = m
            m.sel = 1
            app._settings_start_edit(m)
            for _ in range(10):
                app._handle_modal_key(263)  # backspace
            app._handle_modal_key(10)
            self.assertEqual(m.port, 19132)  # blank keeps the default

    def test_w_hotkey_opens_world_options(self):
        from mcbsadmin.tui import FieldModal

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            app._run_hotkey(ord("W"))
            self.assertIsInstance(app.modal, FieldModal)
            self.assertIn("WORLD OPTIONS", app.modal.title)

    def test_server_ram_reflects_live_usage(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        app.config = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
        app.sys_mem = {"total": 16 * 1024**3, "available": None}
        app.sys_cpu = None
        app.srv_cpu = None
        app.srv_mem = 400 * 1024 * 1024
        app.ext_pid = None
        app.server = type("S", (), {"pid": 1234})()
        rows = app._stats_rows()
        ram = [r for r in rows if r[0] == "Server RAM"][0]
        # Bedrock shows live RSS against the system's total RAM
        self.assertIn("400 MiB", ram[1])
        self.assertIn("16.0 GiB", ram[1])
        self.assertIsNotNone(ram[2])

    def test_no_rcon_row_in_resources(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        app.config = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
        app.sys_mem = {"total": 16 * 1024**3, "available": 8 * 1024**3}
        app.sys_cpu = None
        app.srv_cpu = None
        app.srv_mem = 400 * 1024 * 1024
        app.ext_pid = 99
        app.server = type("S", (), {"pid": 1234})()
        labels = [r[0].lower() for r in app._stats_rows()]
        self.assertNotIn("rcon", labels)

    def test_footer_buttons_include_world_and_no_picker(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        app.server = type(
            "S", (), {"proc": type("P", (), {"poll": lambda s: None})()}
        )()
        app._server_running = lambda: True
        running = app._footer_buttons()
        tokens = [t for _l, t in running]
        # while running, world options must be hidden from the bottom bar
        self.assertNotIn("world", tokens)
        self.assertFalse(any("[W] world" in label for label, _t in running))
        app._server_running = lambda: False
        stopped = app._footer_buttons()
        tokens = [t for _l, t in stopped]
        self.assertIn("world", tokens)
        self.assertTrue(any("[W] world" in label for label, _t in stopped))
        # worlds selector on hotkey V
        self.assertIn("worlds", tokens)
        self.assertTrue(any("[V] worlds" in label for label, _t in stopped))

    def test_settings_hotkey_blocked_while_running(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        app.config = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
        app.config.set("server_dir", tempfile.mkdtemp())
        app.message = ""
        app._message_at = 0.0
        app.log = LogBuffer()
        app.modal = None
        app.prev_modal = None
        app._dirty = set()
        app._server_running = lambda: True
        app._run_hotkey(ord("E"))
        self.assertIsNone(app.modal)
        self.assertTrue(app.message)
        app._run_hotkey(ord("W"))
        self.assertIsNone(app.modal)

    def test_player_actions_kick_and_ban(self):
        from mcbsadmin.tui import PlayerActions

        m = PlayerActions("Steve")
        self.assertIn("Kick player", m.actions)
        self.assertIn("Ban player", m.actions)
        self.assertNotIn("IP ban", m.actions)  # Bedrock has no ban-ip support
        self.assertNotIn("Whitelist", m.actions)

    def test_player_action_kick_prompt_and_send(self):
        from mcbsadmin.tui import App, PlayerActions, PromptModal, KEY_ENTER

        app = App.__new__(App)
        sent = []
        app.server = type("S", (), {"send_command": lambda _, c: sent.append(c)})()
        app.message = ""
        app._message_at = 0.0
        m = PlayerActions("Steve")
        app.modal = m
        app.prev_modal = None
        app._dirty = set()
        app._run_player_action(m, "Kick player")
        self.assertIsInstance(app.modal, PromptModal)
        app.modal.buf = "griefing"
        app._handle_modal_key(KEY_ENTER)
        self.assertEqual(sent, ["kick Steve griefing"])
        self.assertIsNone(app.modal)

    def test_player_action_ban_prompt_and_send(self):
        from mcbsadmin.tui import App, PlayerActions, PromptModal, KEY_ENTER

        app = App.__new__(App)
        sent = []
        app.server = type("S", (), {"send_command": lambda _, c: sent.append(c)})()
        app.message = ""
        app._message_at = 0.0
        m = PlayerActions("Steve")
        app.modal = m
        app.prev_modal = None
        app._dirty = set()
        app._run_player_action(m, "Ban player")
        self.assertIsInstance(app.modal, PromptModal)
        app.modal.buf = "cheating"
        app._handle_modal_key(KEY_ENTER)
        self.assertEqual(sent, ["ban Steve cheating"])
        self.assertIsNone(app.modal)

    def test_worlds_modal_hotkey_list_and_switch(self):
        from mcbsadmin.tui import App, WorldsModal, KEY_ENTER

        td = tempfile.mkdtemp()
        srv_dir = os.path.join(td, "srv")
        worlds_dir = os.path.join(srv_dir, "worlds")
        os.makedirs(os.path.join(worlds_dir, "world_a"))
        os.makedirs(os.path.join(worlds_dir, "world_b"))

        cfg = Config(os.path.join(td, "c.json"))
        cfg.set("server_dir", srv_dir)
        cfg.set("level", "world_a")

        app = App.__new__(App)
        app.config = cfg
        app.server = type("S", (), {"proc": None})()
        app.log = LogBuffer()
        app.message = ""
        app._message_at = 0.0
        app.modal = None
        app.prev_modal = None
        app._dirty = set()
        app._server_running = lambda: False

        app._run_hotkey(ord("V"))
        self.assertIsInstance(app.modal, WorldsModal)
        m = app.modal
        self.assertEqual(m.worlds, ["world_a", "world_b"])
        self.assertEqual(m.current, "world_a")

        m.sel = 1
        app._handle_modal_key(KEY_ENTER)
        self.assertIsNone(app.modal)
        self.assertEqual(cfg.get("level"), "world_b")

    def test_worlds_click_selects_but_does_not_switch(self):
        from mcbsadmin.tui import App, WorldsModal

        td = tempfile.mkdtemp()
        srv_dir = os.path.join(td, "srv")
        worlds_dir = os.path.join(srv_dir, "worlds")
        os.makedirs(os.path.join(worlds_dir, "world_a"))
        os.makedirs(os.path.join(worlds_dir, "world_b"))

        cfg = Config(os.path.join(td, "c.json"))
        cfg.set("server_dir", srv_dir)
        cfg.set("level", "world_a")

        app = App.__new__(App)
        app.config = cfg
        app.server = type("S", (), {"proc": None})()
        app.log = LogBuffer()
        app.message = ""
        app._message_at = 0.0
        app.modal = WorldsModal(["world_a", "world_b"], "world_a")
        app.prev_modal = None
        app._dirty = set()
        app._dispatch_click("worlds:select:world_b")
        # selecting a card only moves the cursor; the switch is explicit
        self.assertIsInstance(app.modal, WorldsModal)
        self.assertEqual(app.modal.sel, 1)
        self.assertEqual(cfg.get("level"), "world_a")

    def test_worlds_buttons_switch_rename_delete_add(self):
        from mcbsadmin.tui import App, ConfirmModal, PromptModal, WorldsModal

        td = tempfile.mkdtemp()
        srv_dir = os.path.join(td, "srv")
        worlds_dir = os.path.join(srv_dir, "worlds")
        os.makedirs(os.path.join(worlds_dir, "world_a"))
        os.makedirs(os.path.join(worlds_dir, "world_b"))

        cfg = Config(os.path.join(td, "c.json"))
        cfg.set("server_dir", srv_dir)
        cfg.set("level", "world_a")

        app = App.__new__(App)
        app.config = cfg
        app.server = type("S", (), {"proc": None})()
        app.log = LogBuffer()
        app.message = ""
        app._message_at = 0.0
        app.modal = WorldsModal(["world_a", "world_b"], "world_a")
        app.prev_modal = None
        app._dirty = set()

        # switch button switches and closes
        app.modal.sel = 1
        app._dispatch_click("worlds:switch")
        self.assertIsNone(app.modal)
        self.assertEqual(cfg.get("level"), "world_b")

        # reopen: add opens the world prompt
        app.modal = WorldsModal(["world_a", "world_b"], "world_b")
        app._dispatch_click("worlds:add")
        self.assertIsInstance(app.modal, PromptModal)
        self.assertEqual(app.modal.submit, "create")

        # rename opens the rename prompt for the selected world
        app.modal = WorldsModal(["world_a", "world_b"], "world_b")
        app.modal.sel = 0
        app._dispatch_click("worlds:rename")
        self.assertIsInstance(app.modal, PromptModal)
        self.assertIn("world_a", app.modal.prompt)
        self.assertEqual(app.modal.submit, "rename")

        # delete opens the confirm dialog
        app.modal = WorldsModal(["world_a", "world_b"], "world_b")
        app.modal.sel = 0
        app._dispatch_click("worlds:delete")
        self.assertIsInstance(app.modal, ConfirmModal)
        # cancelling (default) leaves the world alone
        app._dispatch_click("confirm:cancel")
        self.assertIsInstance(app.modal, WorldsModal)

        # done closes the Worlds menu
        app._dispatch_click("worlds:done")
        self.assertIsNone(app.modal)

    def test_world_card_lines_are_rectangles(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        top, mid, bot = app._world_card_lines("Bedrock level", True, 20)
        self.assertEqual(len(top), 20)
        self.assertEqual(len(mid), 20)
        self.assertEqual(len(bot), 20)
        self.assertTrue(top.startswith("┌"))
        self.assertTrue(top.endswith("┐"))
        self.assertIn("*", top)  # active marker
        inactive_top, _, _ = app._world_card_lines("Bedrock level", False, 20)
        self.assertNotIn("*", inactive_top)

    def test_worlds_bar_tokens(self):
        from mcbsadmin.tui import App

        tokens = [t for _l, t in App._worlds_bar()]
        self.assertIn("worlds:add", tokens)
        self.assertIn("worlds:switch", tokens)
        self.assertIn("worlds:rename", tokens)
        self.assertIn("worlds:delete", tokens)
        self.assertIn("worlds:done", tokens)

    def test_prompt_modal_send_button_submits(self):
        from mcbsadmin.tui import App, PromptModal

        app = App.__new__(App)
        got = []
        m = PromptModal(" KICK PLAYER ", "Reason:", lambda v: got.append(v))
        m.buf = "griefing"
        app.modal = m
        app._dispatch_click("prompt:send")
        self.assertEqual(got, ["griefing"])
        self.assertIsNone(app.modal)

    def test_prompt_modal_cancel_returns_to_previous(self):
        from mcbsadmin.tui import App, PlayerActions, PromptModal

        app = App.__new__(App)
        app.modal = PromptModal(" KICK PLAYER ", "Reason:", lambda v: None)
        app.prev_modal = PlayerActions("Steve")
        app._dispatch_click("prompt:cancel")
        self.assertIsInstance(app.modal, PlayerActions)
        self.assertIsNone(app.prev_modal)

    def test_prompt_modal_send_label(self):
        from mcbsadmin.tui import PromptModal

        self.assertEqual(PromptModal("t", "p", None).send_label(), "[send]")
        self.assertEqual(
            PromptModal("t", "p", None, submit="create").send_label(), "[create]"
        )

    def test_worlds_modal_add_rename_delete(self):
        from mcbsadmin.tui import App, WorldsModal, PromptModal, ConfirmModal, KEY_ENTER

        td = tempfile.mkdtemp()
        srv_dir = os.path.join(td, "srv")
        worlds_dir = os.path.join(srv_dir, "worlds")
        os.makedirs(os.path.join(worlds_dir, "Bedrock level"))

        cfg = Config(os.path.join(td, "c.json"))
        cfg.set("server_dir", srv_dir)
        cfg.set("level", "Bedrock level")

        app = App.__new__(App)
        app.config = cfg
        app.server = type("S", (), {"proc": None})()
        app.log = LogBuffer()
        app.message = ""
        app._message_at = 0.0
        app.modal = WorldsModal(["Bedrock level"], "Bedrock level")
        app.prev_modal = None
        app._dirty = set()
        app._server_running = lambda: False

        app._handle_modal_key(ord("a"))
        self.assertIsInstance(app.modal, PromptModal)
        app.modal.buf = "survival"
        app._handle_modal_key(KEY_ENTER)
        self.assertIsInstance(app.modal, WorldsModal)
        self.assertIn("survival", app.modal.worlds)
        self.assertTrue(os.path.isdir(os.path.join(worlds_dir, "survival")))
        self.assertEqual(cfg.get("level"), "survival")

        m = app.modal
        m.sel = m.worlds.index("survival")
        app._handle_modal_key(ord("r"))
        self.assertIsInstance(app.modal, PromptModal)
        app.modal.buf = "survival_v2"
        app._handle_modal_key(KEY_ENTER)
        self.assertIsInstance(app.modal, WorldsModal)
        self.assertIn("survival_v2", app.modal.worlds)
        self.assertNotIn("survival", app.modal.worlds)
        self.assertTrue(os.path.isdir(os.path.join(worlds_dir, "survival_v2")))
        self.assertEqual(cfg.get("level"), "survival_v2")
        # renaming also updates the world's levelname.txt display name
        ln = os.path.join(worlds_dir, "survival_v2", "levelname.txt")
        self.assertTrue(os.path.isfile(ln))
        with open(ln) as fh:
            self.assertEqual(fh.read(), "survival_v2")

        m = app.modal
        m.sel = m.worlds.index("survival_v2")
        app._handle_modal_key(ord("d"))
        self.assertIsInstance(app.modal, ConfirmModal)
        app.modal.sel = 1
        app._handle_modal_key(KEY_ENTER)
        self.assertIsInstance(app.modal, WorldsModal)
        self.assertNotIn("survival_v2", app.modal.worlds)
        self.assertFalse(os.path.exists(os.path.join(worlds_dir, "survival_v2")))
        self.assertEqual(cfg.get("level"), "Bedrock level")

    def test_set_level_tracks_current_world(self):
        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            app.current_world = app._current_world()
            self.assertEqual(app.current_world, "level")
            app._set_level("survival")
            self.assertEqual(app.current_world, "survival")
            self.assertIn("header", app._dirty)

    def test_world_options_has_online_mode_and_allowlist(self):
        from mcbsadmin.tui import WORLD_FIELDS

        keys = [k for k, _l, _t in WORLD_FIELDS]
        self.assertIn("online-mode", keys)
        self.assertIn("allow-list", keys)
        self.assertNotIn("whitelist", keys)
        self.assertNotIn("hardcore", keys)

    def test_allowlist_row_active_marker(self):
        from mcbsadmin.tui import App, FieldModal, WORLD_FIELDS

        app = App.__new__(App)
        idx = [k for k, _l, _t in WORLD_FIELDS].index("allow-list")
        active = FieldModal(" WORLD OPTIONS ", WORLD_FIELDS,
                            {"allow-list": True}, lambda v: None)
        text = app._field_row_text(active, idx, 40)
        self.assertIn("allowlist >", text)
        inactive = FieldModal(" WORLD OPTIONS ", WORLD_FIELDS,
                              {"allow-list": False}, lambda v: None)
        text = app._field_row_text(inactive, idx, 40)
        self.assertIn("allowlist", text)
        self.assertNotIn("allowlist >", text)

    def test_right_arrow_opens_allowlist_modal(self):
        from mcbsadmin.tui import AllowlistModal, FieldModal, WORLD_FIELDS

        key_right, key_esc = 261, 27  # not defined until curses.setupterm()
        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            m = FieldModal(" WORLD OPTIONS ", WORLD_FIELDS, {}, lambda v: None)
            m.sel = [k for k, _l, _t in WORLD_FIELDS].index("allow-list")
            app.modal = m
            app._handle_field_key(m, key_right)
            self.assertIsInstance(app.modal, AllowlistModal)
            self.assertIs(app.prev_modal, m)
            # ESC returns to world options
            app._handle_modal_key(key_esc)
            self.assertIs(app.modal, m)

    def test_allowlist_add_remove_send_commands(self):
        from mcbsadmin.tui import AllowlistModal

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            sent = []
            app.server = type(
                "S",
                (),
                {"proc": type("P", (), {"poll": lambda self: None})(),
                 "send_command": lambda self, cmd: sent.append(cmd)},
            )()
            m = AllowlistModal(app._allowlist_query)
            m._load()
            m.buf = "Alex"
            app._allowlist_add(m)
            self.assertIn("allowlist add Alex", sent)
            app._allowlist_remove(m, "Alex")
            self.assertIn("allowlist remove Alex", sent)

    def test_allowlist_toggle_flips_file_and_config(self):
        from mcbsadmin.server import read_properties, set_property

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            props = os.path.join(d, "server.properties")
            from mcbsadmin.tui import AllowlistModal

            set_property(props, "allow-list", "false")
            m = AllowlistModal(lambda: [], enabled=False)
            app._allowlist_toggle(m)  # off -> on
            self.assertEqual(read_properties(props)["allow-list"], "true")
            self.assertEqual(m.enabled, True)
            app._allowlist_toggle(m)  # on -> off
            self.assertEqual(read_properties(props)["allow-list"], "false")
            self.assertEqual(m.enabled, False)
            self.assertEqual(
                app.config.get("world", {}).get("allow-list"), "false"
            )

    def test_allowlist_actions_label_follows_state(self):
        from mcbsadmin.tui import AllowlistModal

        self.assertEqual(
            AllowlistModal(lambda: [], enabled=True).toggle_label(),
            "Disable allowlist",
        )
        self.assertEqual(
            AllowlistModal(lambda: [], enabled=False).toggle_label(),
            "Enable allowlist",
        )

    def test_click_on_allowlist_row_opens_editor(self):
        # the allowlist row in World Options is clickable: the mouse hitmap
        # maps its cell to "field:<idx>", which must open the editor
        from mcbsadmin.tui import AllowlistModal, FieldModal, WORLD_FIELDS

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            m = FieldModal(" WORLD OPTIONS ", WORLD_FIELDS, {}, lambda v: None)
            app.modal = m
            idx = [k for k, _l, _t in WORLD_FIELDS].index("allow-list")
            app._dispatch_click(f"field:{idx}")
            self.assertIsInstance(app.modal, AllowlistModal)
            self.assertIs(app.prev_modal, m)

    def test_click_in_allowlist_editor_dispatches(self):
        from mcbsadmin.tui import AllowlistModal

        with tempfile.TemporaryDirectory() as d:
            app = self._app(d)
            app.config.set("server_dir", d)
            sent = []
            app.server = type(
                "S",
                (),
                {"proc": type("P", (), {"poll": lambda self: None})(),
                 "send_command": lambda self, cmd: sent.append(cmd)},
            )()
            m = AllowlistModal(lambda: [])
            app.modal = m
            app._dispatch_click("allowlist-remove:Alex")
            self.assertIn("allowlist remove Alex", sent)
            app._dispatch_click("allowlist-add")
            self.assertTrue(m.editing)
            app._dispatch_click("allowlist-toggle")
            self.assertEqual(m.enabled, True)
            app._dispatch_click("close")
            self.assertIsNone(app.modal)

    def test_bar_only_shows_fill(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        bar = app._bar_only(0.5, 30)
        self.assertIn("[", bar)
        self.assertIn("#", bar)
        self.assertIn("-", bar)

    def test_draw_stats_three_line_layout(self):
        # bar on top, name below, value under it, for a measurable row
        from mcbsadmin.tui import App

        class FakeWin:
            def __init__(self, h, w):
                self.h, self.w = h, w
                self.rows = [""] * h
                self.bordered = False

            def getmaxyx(self):
                return self.h, self.w

            def erase(self):
                pass

            def border(self):
                self.bordered = True

            def addstr(self, y, x, s, _attr=0):
                if 0 <= y < self.h:
                    line = self.rows[y]
                    pad = x - len(line)
                    if pad > 0:
                        line += " " * pad
                    self.rows[y] = line[:x] + s

            def noutrefresh(self):
                pass

        app = App.__new__(App)
        app.sys_mem = {"total": 4 * 1024**3, "available": 3 * 1024**3}
        app.sys_cpu = 25.0
        app.srv_cpu = 50.0
        app.srv_mem = 900 * 1024 * 1024
        app.ext_pid = 123
        app.server = type("S", (), {"pid": 456, "started_at": None,
                                    "status": "running"})()
        app._panes = {}
        win = FakeWin(24, 40)
        app._panes["stats"] = win
        app._draw_stats()
        lines = [l for l in win.rows if l.strip()]
        joined = "\n".join(lines)
        self.assertIn("Server RAM", joined)
        self.assertIn("System RAM", joined)
        # Bedrock shows live RSS without a managed heap
        self.assertIn("900 MiB", joined)
        self.assertIn("1 GiB / 4 GiB", joined)
        self.assertTrue(any(l.strip().startswith("[") and "#" in l and "-" in l
                            for l in lines))

    def test_max_players_loaded_from_properties_default_10(self):
        from mcbsadmin.server import ServerManager
        from mcbsadmin.util import LogBuffer

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(os.path.join(d, "c.json"))
            cfg.set("server_dir", os.path.join(d, "srv"))
            s = ServerManager(cfg, LogBuffer())
            s.setup_files()
            self.assertEqual(s.max_players, 10)
            # a custom max-players is honored
            props = os.path.join(d, "srv", "server.properties")
            with open(props, "a") as fh:
                fh.write("max-players=15\n")
            s._load_max_players()
            self.assertEqual(s.max_players, 15)


class TestStatsBar(unittest.TestCase):
    def test_half_fill_bar(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        line = app._stats_line("players", "10/20", 0.5, 30)
        self.assertIn("10/20", line)
        self.assertIn("#", line)
        self.assertIn("-", line)

    def test_plain_row_when_no_fraction(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        line = app._stats_line("ram", "1 GiB", None, 30)
        self.assertEqual(line, "ram: 1 GiB")

    def test_bar_line_shape(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        line = app._bar_line("players", 0.5, 30)
        self.assertIn("players [", line)
        self.assertTrue(line.strip().endswith("]"))

    def test_centered_is_centered(self):
        from mcbsadmin.tui import App

        line = App._centered("14%", 30)
        self.assertTrue(line.startswith(" "))
        self.assertEqual(len(line.strip()), 3)
        # symmetric padding
        left = len(line) - len(line.lstrip())
        self.assertTrue(abs(left - (28 - 3 - left)) <= 1)

    def test_net_row_left_and_right_aligned(self):
        from mcbsadmin.tui import App

        row = App._net_row("1.2.3.4", "14:32", 40)
        self.assertTrue(row.startswith("1.2.3.4"))
        self.assertTrue(row.rstrip().endswith("14:32"))
        self.assertEqual(len(row), 38)  # fills the content width (w - 2)
        row2 = App._net_row("19132", "Uptime", 40)
        self.assertTrue(row2.startswith("19132"))
        self.assertTrue(row2.rstrip().endswith("Uptime"))

    def test_game_port_default_and_config(self):
        from mcbsadmin.tui import App

        app = App.__new__(App)
        app.config = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
        self.assertEqual(app._game_port(), 19132)  # Bedrock default
        app.config.set("gameport", 25565)
        self.assertEqual(app._game_port(), 25565)

    def test_stats_bottom_block_has_ip_and_port_labels(self):
        from mcbsadmin.tui import App

        class FakeWin:
            def __init__(s, h, w):
                s.h, s.w = h, w
                s.rows = {}

            def getmaxyx(s):
                return s.h, s.w

            def erase(s):
                pass

            def border(s):
                pass

            def addstr(s, y, x, text, _a=0):
                if 0 <= y < s.h:
                    s.rows.setdefault(y, {}).update(
                        dict(enumerate(text, start=x))
                    )

            def noutrefresh(s):
                pass

        app = App.__new__(App)
        app.config = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
        app.sys_mem = {"total": 4 * 1024**3, "available": 3 * 1024**3}
        app.sys_cpu = 25.0
        app.srv_cpu = 50.0
        app.srv_mem = 900 * 1024 * 1024
        app.ext_pid = 123
        app.public_ip = "203.0.113.9"
        app.server = type(
            "S", (), {"pid": 456, "started_at": time.time() - 60,
                      "status": "running"}
        )()
        app._panes = {}
        win = FakeWin(20, 40)
        app._panes["stats"] = win
        app._draw_stats()
        joined = "\n".join(
            "".join(win.rows.get(yy, {}).get(xx, " ") for xx in range(win.w))
            for yy in range(win.h)
        )
        self.assertIn("IP: 203.0.113.9", joined)
        self.assertIn("PORT: 19132", joined)
        self.assertIn("Uptime", joined)
        # the network info is on the left, uptime on the right
        lines = [l for l in joined.splitlines() if "IP:" in l]
        self.assertTrue(lines[0].strip().startswith("IP:"))
        self.assertLess(
            joined.index("IP:"), joined.index("PORT:")
        )


class TestConfig(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "conf.json")
            cfg = Config(path)
            cfg.set("version", "1.26.43.1")
            cfg2 = Config(path)
            self.assertEqual(cfg2.get("version"), "1.26.43.1")

    def test_defaults_are_bedrock(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(os.path.join(d, "c.json"))
            self.assertEqual(cfg.get("gameport"), 19132)
            self.assertEqual(cfg.get("gameportv6"), 19133)

    def test_nested_defaults_are_not_shared(self):
        with tempfile.TemporaryDirectory() as d:
            c1 = Config(os.path.join(d, "a.json"))
            c2 = Config(os.path.join(d, "b.json"))
            c1.set("world", {"difficulty": "hard"})
            self.assertEqual(c2.get("world"), {})

    def test_data_dir_never_relative(self):
        # the "writes into /usr/bin or /usr/share" bug guard: a missing HOME
        # must never produce a path relative to the install/cwd directory.
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            d = default_config_dir()
            self.assertTrue(os.path.isabs(d))
            self.assertNotIn("~", d)

    def test_data_dir_override_env(self):
        with unittest.mock.patch.dict(
            os.environ, {"MCBSADMIN_DATA_DIR": "/tmp/mcs-data"}, clear=True
        ):
            self.assertEqual(default_config_dir(), "/tmp/mcs-data")


class TestBDSInstall(unittest.TestCase):
    def _zip_bytes(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bedrock_server", "#!/bin/sh\n")
            zf.writestr("server.properties", "server-name=fresh\n")
            zf.writestr("worlds/level/level.dat", "fresh-world")
        return buf.getvalue()

    def _install(self, server_dir, zdata, version="1.26.43.1"):
        def fake_dl(url, dest, progress=None, timeout=0):
            with open(dest, "wb") as fh:
                fh.write(zdata)
            return len(zdata)

        with unittest.mock.patch.object(
            versions, "latest_build",
            return_value=(version, "https://x/bedrock-server-x.zip")
        ):
            with unittest.mock.patch.object(
                versions, "download_file", side_effect=fake_dl
            ):
                return versions.install_bedrock(server_dir)

    def _touch_binary(self, server_dir):
        binary = os.path.join(server_dir, "bedrock_server")
        with open(binary, "w") as fh:
            fh.write("x")
        os.chmod(binary, 0o755)

    def test_installs_and_writes_marker(self):
        with tempfile.TemporaryDirectory() as d:
            v_id = self._install(d, self._zip_bytes())
            self.assertEqual(v_id, "1.26.43.1")
            self.assertEqual(versions.read_installed_version(d), "1.26.43.1")
            self.assertTrue(os.path.exists(os.path.join(d, "bedrock_server")))

    def test_skips_download_when_marker_matches(self):
        with tempfile.TemporaryDirectory() as d:
            versions.write_installed_version(d, "1.26.43.1")
            self._touch_binary(d)
            with unittest.mock.patch.object(
                versions, "latest_build",
                return_value=("1.26.43.1", "https://x/b.zip")
            ), unittest.mock.patch.object(
                versions, "download_file"
            ) as dl:
                v_id = versions.install_bedrock(d)
            self.assertEqual(v_id, "1.26.43.1")
            dl.assert_not_called()

    def test_redownloads_when_newer_build_available(self):
        with tempfile.TemporaryDirectory() as d:
            versions.write_installed_version(d, "1.21.50.01")
            self._touch_binary(d)
            v_id = self._install(d, self._zip_bytes())
            self.assertEqual(v_id, "1.26.43.1")
            self.assertEqual(versions.read_installed_version(d), "1.26.43.1")

    def test_update_preserves_worlds_folders_never_rename(self):
        # the core Bedrock guarantee: updating the build replaces the binary
        # but never renames or moves world/allowlist/config folders.
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "worlds", "level"))
            with open(os.path.join(d, "worlds", "level", "level.dat"), "w") as fh:
                fh.write("mine")
            with open(os.path.join(d, "allowlist.json"), "w") as fh:
                json.dump(
                    [{"ignoresPlayerLimit": False, "name": "Alex",
                      "permission": "member"}], fh
                )
            with open(os.path.join(d, "server.properties"), "w") as fh:
                fh.write("server-name=kept\n")
            versions.write_installed_version(d, "1.21.50.01")
            self._touch_binary(d)

            v_id = self._install(d, self._zip_bytes(), "1.26.43.1")
            self.assertEqual(v_id, "1.26.43.1")

            # world survives under its original name
            self.assertTrue(os.path.isdir(os.path.join(d, "worlds", "level")))
            self.assertTrue(
                os.path.exists(os.path.join(d, "worlds", "level", "level.dat"))
            )
            # no version-based folder names were created
            self.assertFalse(os.path.exists(os.path.join(d, "1.26.43.1worlds")))
            self.assertFalse(os.path.exists(os.path.join(d, "worlds1.26.43.1")))
            # allowlist and server.properties preserved
            names = [
                e["name"]
                for e in json.load(open(os.path.join(d, "allowlist.json")))
            ]
            self.assertIn("Alex", names)
            self.assertIn("kept", open(os.path.join(d, "server.properties")).read())
            # binary was refreshed by the new build
            self.assertTrue(
                os.path.exists(os.path.join(d, "bedrock_server"))
            )


class TestStats(unittest.TestCase):
    def test_system_mem_shape(self):
        m = SystemStats.system_mem()
        self.assertIn("total", m)
        self.assertIn("available", m)

    def test_process_cpu_returns_value_after_baseline(self):
        import subprocess
        import time

        if not os.path.exists("/proc"):
            self.skipTest("procfs only")
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; t=time.time()\nwhile time.time()-t<3: pass"]
        )
        try:
            s = SystemStats()
            self.assertIsNone(s.process_cpu(p.pid))  # baseline sample
            time.sleep(1.0)
            val = s.process_cpu(p.pid)
            self.assertIsNotNone(val)
            self.assertGreaterEqual(val, 0.0)
            self.assertLessEqual(val, 100.0)
        finally:
            p.kill()


class TestSetProperty(unittest.TestCase):
    def _path(self, d):
        p = os.path.join(d, "server.properties")
        with open(p, "w") as fh:
            fh.write("# comment\ngamemode=survival\nallow-list=true\n")
        return p

    def test_updates_existing_key_in_place(self):
        from mcbsadmin.server import read_properties, set_property

        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            set_property(p, "allow-list", "false")
            text = open(p).read()
            self.assertIn("# comment", text)
            self.assertIn("gamemode=survival", text)
            self.assertEqual(read_properties(p)["allow-list"], "false")

    def test_appends_when_absent(self):
        from mcbsadmin.server import read_properties, set_property

        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            set_property(p, "server-name", "hello")
            self.assertEqual(read_properties(p)["server-name"], "hello")
            self.assertEqual(read_properties(p)["allow-list"], "true")

    def test_creates_file_if_missing(self):
        from mcbsadmin.server import read_properties, set_property

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "new.properties")
            set_property(p, "allow-list", "false")
            self.assertEqual(read_properties(p)["allow-list"], "false")


class TestAllowlistFiles(unittest.TestCase):
    def test_add_remove_read_roundtrip(self):
        from mcbsadmin.server import (
            add_allowlist_entry,
            read_allowlist_file,
            remove_allowlist_entry,
        )

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "allowlist.json")
            self.assertTrue(add_allowlist_entry(p, "Alex"))
            self.assertFalse(add_allowlist_entry(p, "Alex"))  # no dupes
            self.assertTrue(add_allowlist_entry(p, "Bob"))
            self.assertEqual(read_allowlist_file(p), ["Alex", "Bob"])
            self.assertTrue(remove_allowlist_entry(p, "Alex"))
            self.assertEqual(read_allowlist_file(p), ["Bob"])

    def test_written_entries_are_valid_allowlist_format(self):
        from mcbsadmin.server import write_allowlist_file

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "allowlist.json")
            write_allowlist_file(p, ["Alex"])
            data = json.load(open(p))
            self.assertEqual(data[0]["name"], "Alex")
            self.assertIn("ignoresPlayerLimit", data[0])
            self.assertNotIn("uuid", data[0])

    def test_read_missing_file(self):
        from mcbsadmin.server import read_allowlist_file

        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                read_allowlist_file(os.path.join(d, "nope.json")), []
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)