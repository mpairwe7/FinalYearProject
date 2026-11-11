"""The container runs two API roles and nginx sends every call path to one.

G306 (docs/plans/receptionist-call-process-2026-11-12.md): a live call lives in
one process's memory, so the Space runs a ``calls`` process on 8082 that owns
every call path and a ``chat`` process on 8081 for the rest. These tests read
the deployment files the way the container does: the launcher script is run (in
dry-run mode, so it prints what it would exec), supervisord.conf is parsed, and
nginx.conf is read as text, because neither supervisord nor nginx is available
where the suite runs. ``nginx -t`` and the real boot are the image check's job
(``verify-embedded-stores.sh``).
"""

from __future__ import annotations

import configparser
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from app.process_role import NGINX_CALL_LOCATION

_BACKEND = Path(__file__).resolve().parents[1]
_APP = _BACKEND.parent
CRANECLOUD = _APP / "deploy" / "cranecloud"
SCRIPT = CRANECLOUD / "uvicorn-role.sh"
SUPERVISORD_CONF = CRANECLOUD / "supervisord.conf"
NGINX_CONF = CRANECLOUD / "nginx.conf"
VERIFY_SCRIPT = CRANECLOUD / "verify-embedded-stores.sh"
CRANECLOUD_DOCKERFILE = _APP / "Dockerfile.cranecloud"
SPACE_DOCKERFILE = _APP / "deploy" / "hf-space" / "Dockerfile"


def _run_role_script(role: str, metrics_dir: str, **env: str) -> subprocess.CompletedProcess[str]:
    """``uvicorn-role.sh <role>`` in dry-run mode: it prints the launch instead of exec'ing it.

    Only PATH is inherited, so a ``UVICORN_WORKERS`` or ``URA_*`` variable on the
    machine running the tests cannot leak into the answer.
    """
    environment = {
        "PATH": os.environ["PATH"],
        "PROMETHEUS_MULTIPROC_DIR": metrics_dir,
        "LOG_LEVEL": "info",
        "URA_DRY_RUN": "1",
    }
    environment.update(env)
    return subprocess.run(
        ["sh", str(SCRIPT), role],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _launch_workers(output: str) -> int:
    match = re.search(r"--workers (\d+)\b", output)
    assert match, f"no --workers in the launch line:\n{output}"
    return int(match.group(1))


def _supervisord() -> configparser.ConfigParser:
    # supervisord's own reader treats ";" as an inline comment and its %(ENV_x)s
    # expansions are not interpolated here.
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    parser.read(SUPERVISORD_CONF, encoding="utf-8")
    return parser


def _nginx_text() -> str:
    """nginx.conf with its comments removed."""
    return re.sub(r"(?m)#.*$", "", NGINX_CONF.read_text(encoding="utf-8"))


def _regex_locations(text: str) -> list[tuple[str, str]]:
    """Every ``location ~ <regex> { ... }`` in file order as (regex, body).

    nginx tries regex locations in this order and the first match wins, so the
    order is part of the behaviour.
    """
    found = []
    for opening in re.finditer(r"location\s+~\s+(\S+)\s*\{", text):
        depth, cursor = 1, opening.end()
        while depth:
            brace = re.compile(r"[{}]").search(text, cursor)
            assert brace, f"unbalanced braces after location {opening.group(1)}"
            depth += 1 if brace.group() == "{" else -1
            cursor = brace.end()
        found.append((opening.group(1), text[opening.end() : cursor - 1]))
    return found


def _upstream(text: str, name: str) -> str | None:
    match = re.search(rf"upstream\s+{name}\s*\{{([^}}]*)\}}", text)
    return match.group(1) if match else None


class RoleScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.metrics_dir = str(Path(tmp.name) / "prometheus")

    def test_the_script_starts_each_role_on_its_port(self) -> None:
        chat = _run_role_script("chat", self.metrics_dir)
        self.assertEqual(chat.returncode, 0, chat.stderr)
        self.assertIn("--port 8081", chat.stdout)
        self.assertIn("URA_PROCESS_ROLE=chat", chat.stdout)
        self.assertIn("URA_CALLS_PROCESS_URL=http://127.0.0.1:8082", chat.stdout)

        calls = _run_role_script("calls", self.metrics_dir)
        self.assertEqual(calls.returncode, 0, calls.stderr)
        self.assertIn("--port 8082 --workers 1", calls.stdout)
        self.assertIn("UVICORN_WORKERS=1", calls.stdout)
        self.assertIn("URA_PROCESS_ROLE=calls", calls.stdout)

        bogus = _run_role_script("bogus", self.metrics_dir)
        self.assertEqual(bogus.returncode, 2, bogus.stdout + bogus.stderr)
        no_role = subprocess.run(
            ["sh", str(SCRIPT)],
            env={
                "PATH": os.environ["PATH"],
                "PROMETHEUS_MULTIPROC_DIR": self.metrics_dir,
                "URA_DRY_RUN": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(no_role.returncode, 2, no_role.stdout + no_role.stderr)

    def test_the_launch_is_the_command_supervisord_used_to_run(self) -> None:
        chat = _run_role_script("chat", self.metrics_dir, UVICORN_WORKERS="2", LOG_LEVEL="warning")
        self.assertIn(
            "/usr/local/bin/wait-for-qdrant.sh /opt/venv/bin/uvicorn app.main:app --host 127.0.0.1 "
            "--port 8081 --workers 1 --log-level warning --proxy-headers --forwarded-allow-ips=*",
            chat.stdout,
        )

    def test_the_chat_process_always_points_at_the_local_call_process(self) -> None:
        chat = _run_role_script(
            "chat", self.metrics_dir, URA_CALLS_PROCESS_URL="http://elsewhere:9"
        )
        self.assertIn("URA_CALLS_PROCESS_URL=http://127.0.0.1:8082", chat.stdout)
        self.assertNotIn("elsewhere", chat.stdout)

    def test_chat_workers_are_one_fewer_than_the_setting(self) -> None:
        # Blank, junk and zero count as the image default of 2, so they leave one chat
        # worker; one setting leaves one too, because a chat process needs a worker.
        expected = {
            "": 1,
            "abc": 1,
            "0": 1,
            "1": 1,
            "2": 1,
            "4": 3,
            "08": 7,
            "3.5": 1,
            "-3": 1,
            " 4": 1,
        }
        for setting, chat_workers in expected.items():
            with self.subTest(UVICORN_WORKERS=setting):
                chat = _run_role_script("chat", self.metrics_dir, UVICORN_WORKERS=setting)
                self.assertEqual(chat.returncode, 0, chat.stderr)
                self.assertEqual(_launch_workers(chat.stdout), chat_workers)
                calls = _run_role_script("calls", self.metrics_dir, UVICORN_WORKERS=setting)
                self.assertEqual(_launch_workers(calls.stdout), 1)
                self.assertIn("UVICORN_WORKERS=1", calls.stdout)

    def test_an_unset_worker_setting_counts_as_two(self) -> None:
        chat = _run_role_script("chat", self.metrics_dir)  # no UVICORN_WORKERS at all
        self.assertEqual(chat.returncode, 0, chat.stderr)
        self.assertEqual(_launch_workers(chat.stdout), 1)

    def test_the_metrics_directory_is_cleared_once_per_container_start(self) -> None:
        metrics = Path(self.metrics_dir)
        metrics.mkdir()
        (metrics / "counter_1.db").write_text("stale")
        (metrics / "notes.txt").write_text("not a metrics file")

        first = _run_role_script("chat", self.metrics_dir)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(
            (metrics / "counter_1.db").exists(),
            "a container start must drop the last run's samples",
        )
        self.assertTrue((metrics / "notes.txt").exists(), "only *.db files are the samples")
        self.assertTrue((metrics / ".reset").exists())

        # A program restart inside the running container: the other role's workers are
        # writing here, so their samples must survive.
        (metrics / "counter_2.db").write_text("live")
        second = _run_role_script("calls", self.metrics_dir)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(
            (metrics / "counter_2.db").exists(), "a program restart must not clear the directory"
        )
        third = _run_role_script("chat", self.metrics_dir)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertTrue((metrics / "counter_2.db").exists())

        # A new container has a new filesystem: no marker, so it clears again.
        with tempfile.TemporaryDirectory() as other:
            fresh = Path(other) / "prometheus"
            fresh.mkdir()
            (fresh / "counter_9.db").write_text("stale")
            self.assertEqual(_run_role_script("calls", str(fresh)).returncode, 0)
            self.assertFalse((fresh / "counter_9.db").exists())

    def test_the_metrics_directory_is_created_when_it_is_missing(self) -> None:
        self.assertFalse(Path(self.metrics_dir).exists())
        result = _run_role_script("calls", self.metrics_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((Path(self.metrics_dir) / ".reset").exists())

    def test_the_two_roles_starting_together_clear_once(self) -> None:
        metrics = Path(self.metrics_dir)
        metrics.mkdir()
        (metrics / "counter_1.db").write_text("stale")
        processes = []
        for role in ("chat", "calls"):
            environment = {
                "PATH": os.environ["PATH"],
                "PROMETHEUS_MULTIPROC_DIR": self.metrics_dir,
                "URA_DRY_RUN": "1",
            }
            processes.append(
                subprocess.Popen(
                    ["sh", str(SCRIPT), role],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        for process in processes:
            self.assertEqual(process.wait(timeout=30), 0)
        self.assertFalse((metrics / "counter_1.db").exists())
        self.assertTrue((metrics / ".reset").exists())


class SupervisordTests(unittest.TestCase):
    def test_supervisord_runs_both_roles(self) -> None:
        conf = _supervisord()
        self.assertFalse(
            conf.has_section("program:uvicorn"), "the single uvicorn program is replaced"
        )
        commands = {}
        for role in ("chat", "calls"):
            section = f"program:uvicorn-{role}"
            self.assertTrue(conf.has_section(section), f"{section} is missing")
            commands[role] = conf.get(section, "command").strip()
            self.assertEqual(commands[role], f"/usr/local/bin/uvicorn-role.sh {role}")
        for section in conf.sections():
            if conf.has_option(section, "command"):
                self.assertNotIn("-delete", conf.get(section, "command"), section)

    def test_both_roles_keep_the_program_settings_the_single_one_had(self) -> None:
        conf = _supervisord()
        environments = []
        for role in ("chat", "calls"):
            section = f"program:uvicorn-{role}"
            with self.subTest(section=section):
                self.assertEqual(conf.get(section, "directory"), "/app/backend")
                self.assertEqual(conf.get(section, "autostart"), "true")
                self.assertEqual(conf.get(section, "autorestart"), "true")
                self.assertEqual(conf.get(section, "priority"), "20")
                self.assertEqual(conf.get(section, "startsecs"), "5")
                self.assertEqual(conf.get(section, "startretries"), "3")
                self.assertEqual(conf.get(section, "stdout_logfile"), "/dev/stdout")
                self.assertEqual(conf.get(section, "stdout_logfile_maxbytes"), "0")
                self.assertEqual(conf.get(section, "stderr_logfile"), "/dev/stderr")
                self.assertEqual(conf.get(section, "stderr_logfile_maxbytes"), "0")
                environment = conf.get(section, "environment")
                for name in (
                    "PYTHONUNBUFFERED",
                    "PYTHONDONTWRITEBYTECODE",
                    "PATH",
                    "PROMETHEUS_MULTIPROC_DIR",
                ):
                    self.assertIn(f'{name}="', environment)
                environments.append(environment)
        metrics_dirs = {
            re.search(r'PROMETHEUS_MULTIPROC_DIR="([^"]*)"', e).group(1) for e in environments
        }
        self.assertEqual(
            len(metrics_dirs), 1, "both roles must write their samples to one directory"
        )


class NginxTests(unittest.TestCase):
    def test_nginx_sends_every_call_path_to_the_call_process(self) -> None:
        text = _nginx_text()
        calls_upstream = _upstream(text, "calls")
        api_upstream = _upstream(text, "api")
        self.assertIsNotNone(calls_upstream, "upstream calls is missing")
        self.assertIsNotNone(api_upstream, "upstream api is missing")
        self.assertEqual(re.findall(r"server\s+(\S+?);", calls_upstream), ["127.0.0.1:8082"])
        self.assertEqual(
            re.findall(r"server\s+(\S+?);", api_upstream), ["127.0.0.1:8081", "127.0.0.1:8082"]
        )
        self.assertIsNone(
            _upstream(text, "backend"), "nothing may still use the old single upstream"
        )
        self.assertNotIn("http://backend", text)

        locations = _regex_locations(text)
        self.assertTrue(locations, "no regex locations found")
        regex, body = locations[0]
        self.assertEqual(
            regex,
            NGINX_CALL_LOCATION,
            "the call location must come first: nginx takes the first regex match",
        )
        self.assertEqual(sum(1 for r, _ in locations if r == NGINX_CALL_LOCATION), 1)
        for directive in (
            "proxy_pass http://calls;",
            "rewrite ^/api/(.*)$ /$1 break;",
            "proxy_set_header Upgrade $http_upgrade;",
            "proxy_set_header Connection $connection_upgrade;",
            "proxy_read_timeout 3600s;",
            "proxy_send_timeout 3600s;",
        ):
            self.assertIn(directive, body)
        self.assertIn(
            f"location ~ {NGINX_CALL_LOCATION} {{", NGINX_CONF.read_text(encoding="utf-8")
        )

        # Everything else goes to the shared upstream, and only the call location to the call process.
        for other_regex, other_body in locations[1:]:
            with self.subTest(location=other_regex):
                self.assertNotIn("http://calls", other_body)
                self.assertIn("proxy_pass http://api;", other_body)

    def test_the_call_location_matches_exactly_the_call_paths(self) -> None:
        pattern = re.compile(NGINX_CALL_LOCATION)
        for path in (
            "/api/v1/calls/stream",
            "/v1/calls/stream",
            "/api/admin/calls",
            "/v1/admin/calls/",
            "/api/v1/admin/calls/c1/audio",
            "/v1/admin/officers/me/presence",
            # nginx matches a location against the URI without its query string.
            urlsplit("/v1/admin/calls?status=live").path,
            urlsplit("/api/v1/admin/calls/?status=live").path,
        ):
            with self.subTest(accepts=path):
                self.assertIsNotNone(pattern.fullmatch(path))
        for path in (
            "/calls",
            "/calls/abc",
            "/v1/chat",
            "/v1/admin/flags",
            "/v1/admin/tickets/t1/presence",
        ):
            with self.subTest(rejects=path):
                self.assertIsNone(pattern.fullmatch(path))

    def test_the_frontend_page_for_calls_is_not_proxied_to_the_api(self) -> None:
        for regex, _ in _regex_locations(_nginx_text()):
            for path in ("/calls", "/calls/abc"):
                with self.subTest(location=regex, path=path):
                    self.assertIsNone(re.search(regex, path))

    def test_the_old_websocket_location_no_longer_names_the_call_sockets(self) -> None:
        websocket = [(r, b) for r, b in _regex_locations(_nginx_text()) if "voice/chat/stream" in r]
        self.assertEqual(len(websocket), 1)
        regex, body = websocket[0]
        self.assertNotIn("calls/stream", regex)
        self.assertIn("admin/tickets/stream", regex)
        self.assertIn("proxy_pass http://api;", body)


class SpaceAndImageTests(unittest.TestCase):
    def test_the_space_declares_one_replica(self) -> None:
        lines = SPACE_DOCKERFILE.read_text(encoding="utf-8").splitlines()
        instructions = [
            line for line in lines if line.strip() and not line.lstrip().startswith("#")
        ]
        from_index = next(
            i
            for i, line in enumerate(instructions)
            if line.startswith("FROM landwind/ura-chatbot:")
        )
        self.assertEqual(
            instructions[from_index + 1],
            "ENV VOICE_RECEPTIONIST_REPLICAS=1",
            "the ENV line sits right after FROM",
        )

    def test_the_image_ships_the_role_script_executable(self) -> None:
        dockerfile = CRANECLOUD_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "COPY App/deploy/cranecloud/uvicorn-role.sh /usr/local/bin/uvicorn-role.sh", dockerfile
        )
        self.assertIn("chmod +x /usr/local/bin/uvicorn-role.sh", dockerfile)
        self.assertIn("sh -n /usr/local/bin/uvicorn-role.sh", dockerfile)

    def test_the_image_check_covers_both_roles_and_nginx(self) -> None:
        script = VERIFY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("program:uvicorn-chat", script)
        self.assertIn("program:uvicorn-calls", script)
        self.assertIn("nginx -t", script)


if __name__ == "__main__":
    unittest.main()
