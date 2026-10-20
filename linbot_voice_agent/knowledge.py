import json
from importlib.resources import files
from pathlib import Path

class Knowledge:
    def __init__(self, path=None):
        raw = Path(path).read_text(encoding="utf-8") if path else files("linbot_voice_agent").joinpath("data/hospital.example.json").read_text(encoding="utf-8")
        self.data = json.loads(raw)
        self.places = {p["id"]: p for p in self.data["places"]}
        if len(self.places) != len(self.data["places"]) or not self.places:
            raise ValueError("地点 ID 必须唯一且非空")
        for p in self.places.values():
            for key in ("id", "name", "location", "route"):
                if not isinstance(p[key], str) or not p[key].strip():
                    raise ValueError("地点字段不能为空")
            if not isinstance(p["aliases"], list) or not all(isinstance(a,str) and a for a in p["aliases"]):
                raise ValueError("aliases 必须是非空字符串列表")
        self.prefix = "【示例数据】" if self.data.get("is_example", True) else ""

    def match(self, text):
        return [p["id"] for p in self.places.values() if any(a in text for a in [p["name"], *p["aliases"]])]
