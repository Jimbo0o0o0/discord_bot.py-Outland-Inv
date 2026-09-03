import aiofiles
import asyncio
import json
import os
import copy
import shutil
import tempfile
from typing import Any, Callable, Dict, Optional

class AsyncJSONStorage:
    def __init__(
        self,
        filename: str = "data.json",
        save_delay: float = 1.0,  # Reduced for faster saves
        backup_count: int = 2,
        on_change: Optional[Callable[[str, str, Any, Any], None]] = None,
        logger=None,  # Optional logger for integration with bot
    ):
        self.filename = filename
        self.save_delay = save_delay
        self.backup_count = backup_count
        self.on_change = on_change
        self.logger = logger  # Bot's logger (e.g., DiscordBotLogger)

        self.lock = asyncio.Lock()
        self._data: Dict[str, Any] = {}
        self._last_saved: Dict[str, Any] = {}
        self._save_task: Optional[asyncio.Task] = None
        parent = os.path.dirname(os.path.abspath(self.filename))
        if parent:
            os.makedirs(parent, exist_ok=True)

    async def __aenter__(self):
        await self.load()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.flush()

    async def load(self) -> None:
        """Load JSON data from disk."""
        if not os.path.exists(self.filename):
            self._data = {}
            self._last_saved = {}
            return

        async with self.lock:
            async with aiofiles.open(self.filename, "r") as f:
                try:
                    content = await f.read()
                    self._data = json.loads(content) if content.strip() else {}
                except json.JSONDecodeError as e:
                    if self.logger:
                        self.logger.base_logger.error(f"Failed to parse JSON file {self.filename}: {e}")
                    else:
                        print(f"[AsyncJSONStorage] Failed to parse JSON file {self.filename}: {e}")
                    # Backup corrupted file
                    if os.path.exists(self.filename):
                        try:
                            os.replace(self.filename, f"{self.filename}.corrupted")
                            if self.logger:
                                self.logger.base_logger.info(f"Backed up corrupted file to {self.filename}.corrupted")
                        except Exception as ex:
                            if self.logger:
                                self.logger.base_logger.error(f"Failed to backup corrupted file: {ex}")
                    self._data = {}
            await self.migrate()
            self._last_saved = copy.deepcopy(self._data)

    async def save(self, force: bool = False) -> None:
        """Write data to disk if changed or forced."""
        async with self.lock:
            changed = self._data != self._last_saved
            if not changed and not force:
                return

            parent = os.path.dirname(os.path.abspath(self.filename)) or "."
            temp_filename = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=parent,
                    prefix=f".{os.path.basename(self.filename)}.",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    temp_filename = temp_file.name
                    json.dump(self._data, temp_file, indent=4)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())

                await self._rotate_backups()
                os.replace(temp_filename, self.filename)
                temp_filename = None
            finally:
                if temp_filename and os.path.exists(temp_filename):
                    os.unlink(temp_filename)

            self._last_saved = copy.deepcopy(self._data)
            if self.logger:
                self.logger.base_logger.info(f"Saved data to {self.filename}")

    async def _rotate_backups(self):
        if not os.path.exists(self.filename):
            return
        """Keep limited numbered backups (e.g. file.json.bak1, .bak2)."""
        for i in range(self.backup_count, 0, -1):
            src = f"{self.filename}.bak{i-1}" if i > 1 else self.filename
            dst = f"{self.filename}.bak{i}"
            if os.path.exists(src):
                try:
                    if i == 1:
                        shutil.copy2(src, dst)
                    else:
                        os.replace(src, dst)
                    if self.logger:
                        self.logger.base_logger.debug(f"Rotated backup {src} to {dst}")
                except Exception as e:
                    if self.logger:
                        self.logger.base_logger.error(f"Failed to rotate backup {src} to {dst}: {e}")
                    else:
                        print(f"[AsyncJSONStorage] Failed to rotate backup {src} to {dst}: {e}")

    async def flush(self):
        """Force immediate save."""
        if self._save_task:
            self._save_task.cancel()
            self._save_task = None
        await self.save(force=True)

    async def _delayed_save(self):
        try:
            await asyncio.sleep(self.save_delay)
            await self.save()
        except asyncio.CancelledError:
            pass

    async def schedule_save(self):
        """Schedule a save after delay (debounced)."""
        if self._save_task:
            self._save_task.cancel()
        self._save_task = asyncio.create_task(self._delayed_save())

    def _get_collection(self, name: str) -> Dict[str, Any]:
        if name not in self._data:
            self._data[name] = {}
        return self._data[name]

    async def get(self, collection: str, key: str, default: Any = None) -> Any:
        async with self.lock:
            value = self._get_collection(collection).get(str(key), default)
            return copy.deepcopy(value)

    async def set(self, collection: str, key: str, value: Any, save: bool = True) -> None:
        """Set a value, trigger on_change, and optionally schedule save."""
        async with self.lock:
            coll = self._get_collection(collection)
            key = str(key)
            old_value = coll.get(key)
            coll[key] = value

            if self.on_change:
                try:
                    self.on_change(collection, key, old_value, value)
                except Exception as e:
                    if self.logger:
                        self.logger.base_logger.error(f"on_change error: {e}")
                    else:
                        print(f"[AsyncJSONStorage] on_change error: {e}")

            if save:
                await self.schedule_save()

    async def delete(self, collection: str, key: str, save: bool = True) -> bool:
        """Delete a key and trigger on_change with old_value -> None."""
        async with self.lock:
            coll = self._get_collection(collection)
            key = str(key)
            if key in coll:
                old_value = coll[key]
                del coll[key]

                if self.on_change:
                    try:
                        self.on_change(collection, key, old_value, None)
                    except Exception as e:
                        if self.logger:
                            self.logger.base_logger.error(f"on_change error: {e}")
                        else:
                            print(f"[AsyncJSONStorage] on_change error: {e}")

                if save:
                    await self.schedule_save()
                return True
            return False

    async def query(self, collection: str, filter_func: Callable[[Any], bool]) -> Dict[str, Any]:
        """Return all items matching filter_func."""
        coll = self._get_collection(collection)
        return {k: v for k, v in coll.items() if filter_func(v)}

    async def all(self, collection: str):
        """Return list of all values in a collection."""
        return list(self._get_collection(collection).values())
    
    async def all_dict(self, collection: str) -> Dict[str, Any]:
        """Return a dictionary of key -> value pairs for the given collection."""
        return self._get_collection(collection)
    
    async def count(self, collection: str):
        """Return number of items in a collection."""
        return len(self._get_collection(collection))

    async def migrate(self):
        """Initialize default structure for log levels."""
        if "_meta" not in self._data:
            self._data["_meta"] = {"version": 1}

        await self.schedule_save()