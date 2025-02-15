import os
import sys

from sqlitedict import SqliteDict

_current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.extend([_current_dir])

from src.constant import MAX_HISTORY_LEN


class KVWrapper(object):
    def __init__(self, kv_name):
        self._db = SqliteDict(filename=kv_name)

    def get(self, key: str):
        v = self._db[key]
        if v is None:
            raise KeyError(key)
        return v

    def put(self, key: str, value: str):
        self._db[key] = value
        self._db.commit()

    def append(self, key: str, value):
        """记录聊天历史"""
        self._db[key] = self._db.get(key, [])
        # 最长记录的对话轮数 MAX_HISTORY_LEN
        _ = self._db[key][-MAX_HISTORY_LEN:]
        _.append(value)
        self._db[key] = _
        self._db.commit()
