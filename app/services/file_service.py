import os
import time


class FileService:
    @staticmethod
    def format_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024
        return f"{size:.1f} PB"

    @staticmethod
    def _relevance_score(name_lower: str, q: str) -> float:
        """검색어와 파일/폴더 이름이 얼마나 잘 맞는지 점수화.
        완전 일치 > 접두 일치 > 단어 경계(구분자 뒤) 일치 > 중간 부분 일치 순으로 기본 점수를 주고,
        검색어가 이름에서 차지하는 비중이 클수록, 매치 위치가 앞쪽일수록 가점을 준다."""
        idx = name_lower.find(q)
        if idx == -1:
            return -1.0
        if name_lower == q:
            return 100.0
        if name_lower.startswith(q):
            return 90.0
        at_boundary = not name_lower[idx - 1].isalnum()
        base = 75.0 if at_boundary else 55.0
        coverage = len(q) / len(name_lower)
        position = 1 - (idx / len(name_lower))
        return base + coverage * 15 + position * 10

    @staticmethod
    def search_recursive(
        root: str,
        query: str,
        max_results: int = 500,
        collect_limit: int = 3000,
        time_budget: float = 8.0,
    ) -> tuple:
        """이름에 검색어가 부분적으로라도 포함되면 매치시키고, 관련도 점수가 높은 순으로
        정렬해 반환한다. 디스크 전체를 끝까지 훑느라 오래 걸리는 걸 막기 위해
        - collect_limit: 후보를 이 개수만큼 모으면 더 이상 순회하지 않고
        - time_budget: 이 시간(초)을 넘기면 그 시점까지 찾은 것만으로 마무리한다.
        둘 중 하나라도 걸리면 두 번째 반환값(truncated)이 True가 된다.
        """
        q = query.strip().lower()
        if not q:
            return [], False

        matches = []  # (score, is_dir, full_path, name)
        truncated = False
        deadline = time.monotonic() + time_budget
        stack = [root]

        while stack:
            if len(matches) >= collect_limit or time.monotonic() > deadline:
                truncated = True
                break
            dirpath = stack.pop()
            try:
                with os.scandir(dirpath) as it:
                    for entry in it:
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        if is_dir:
                            if entry.name == ".trash":
                                continue
                            stack.append(entry.path)
                        score = FileService._relevance_score(entry.name.lower(), q)
                        if score >= 0:
                            matches.append((score, is_dir, entry.path, entry.name))
                            if len(matches) >= collect_limit:
                                truncated = True
                                break
            except (PermissionError, OSError):
                continue
            if time.monotonic() > deadline:
                truncated = True
                break

        matches.sort(key=lambda m: -m[0])
        if len(matches) > max_results:
            truncated = True

        results = []
        for score, is_dir, full, name in matches[:max_results]:
            if is_dir:
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    mtime = 0
                results.append(
                    {
                        "name": name, "path": full, "is_dir": True,
                        "size": "", "size_raw": 0, "mtime": mtime, "score": score,
                    }
                )
            else:
                try:
                    size_raw = os.path.getsize(full)
                    size = FileService.format_size(size_raw)
                    mtime = os.path.getmtime(full)
                except OSError:
                    size_raw, size, mtime = 0, "", 0
                results.append(
                    {
                        "name": name, "path": full, "is_dir": False,
                        "size": size, "size_raw": size_raw, "mtime": mtime, "score": score,
                    }
                )
        return results, truncated
