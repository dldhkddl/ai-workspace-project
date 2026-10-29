from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str


class GitExecutor:
    """
    시스템이 실행하는 모든 git 명령의 단일 통로.

    hook·서명·작성자를 호출마다 챙기면 한 곳만 빠뜨려도 그게 구멍이 된다.
    hook은 저장소 설정을 건드리지 않고 명령 단위 `-c` 로만 끄며, 대상은
    운영센터가 관리하는 빈 폴더다. `/dev/null` 대신 빈 폴더를 쓰는 이유는
    Windows에서도 같은 방식이 그대로 통하기 때문이다.

    사용자의 전역 설정(core.autocrlf 등)은 일부러 그대로 둔다. 분리 작업공간이
    사용자가 보는 체크아웃과 다르게 동작하면 변경 내용을 비교할 수 없다.
    """

    def __init__(self, *, hooks_path: Path, author_name: str, author_email: str) -> None:
        self.hooks_path = hooks_path
        self.author_name = author_name
        self.author_email = author_email

    def _base_command(self) -> list[str]:
        self.hooks_path.mkdir(parents=True, exist_ok=True)
        return [
            "git",
            "-c", "core.quotepath=false",
            "-c", f"core.hooksPath={self.hooks_path.as_posix()}",
            "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false",
            "-c", f"user.name={self.author_name}",
            "-c", f"user.email={self.author_email}",
        ]

    def _environment(self) -> dict[str, str]:
        # GIT_DIR·GIT_WORK_TREE·GIT_INDEX_FILE·GIT_CONFIG_* 같은 변수가 환경에 남아 있으면
        # 우리가 지정한 cwd 와 무관한 저장소나 인덱스를 건드릴 수 있다. GIT_ 로 시작하는
        # 값은 통째로 걷어낸 뒤 필요한 것만 다시 넣는다.
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        environment.update({
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email,
            "GIT_TERMINAL_PROMPT": "0",
        })
        return environment

    def run(self, cwd: Path, *args: str, check: bool = True) -> GitCommandResult:
        completed = subprocess.run(
            [*self._base_command(), *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._environment(),
        )
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                completed.args,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)

    def output(self, cwd: Path, *args: str) -> str:
        return self.run(cwd, *args).stdout

    def line(self, cwd: Path, *args: str) -> str:
        return self.output(cwd, *args).strip()
