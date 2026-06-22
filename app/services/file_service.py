import os


class FileService:
    @staticmethod
    def format_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024
        return f"{size:.1f} PB"

    @staticmethod
    def search_recursive(root: str, query: str, max_results: int = 500) -> list:
        results = []
        q = query.lower()
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                for name in dirnames:
                    if q in name.lower():
                        full = os.path.join(dirpath, name)
                        results.append(
                            {"name": name, "path": full, "is_dir": True, "size": ""}
                        )
                        if len(results) >= max_results:
                            return results
                for name in filenames:
                    if q in name.lower():
                        full = os.path.join(dirpath, name)
                        try:
                            size = FileService.format_size(os.path.getsize(full))
                        except OSError:
                            size = ""
                        results.append(
                            {"name": name, "path": full, "is_dir": False, "size": size}
                        )
                        if len(results) >= max_results:
                            return results
        except PermissionError, OSError:
            pass
        return results
