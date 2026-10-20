"""Small, inspectable hospital service corpus with lexical retrieval and provenance."""
import json
import re
from datetime import date
from importlib.resources import files
from pathlib import Path


def terms(text):
    text = text.lower()
    tokens = set(re.findall(r'[a-z0-9_]+', text))
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class Knowledge:
    def __init__(self, path=None):
        raw = Path(path).read_text(encoding='utf-8') if path else files('linbot_voice_agent').joinpath('data/hospital.example.json').read_text(encoding='utf-8')
        self.data = json.loads(raw)
        self.places = {p['id']: p for p in self.data['places']}
        if len(self.places) != len(self.data['places']) or not self.places:
            raise ValueError('地点 ID 必须唯一且非空')
        for p in self.places.values():
            for key in ('id', 'name', 'location', 'route'):
                if not isinstance(p[key], str) or not p[key].strip():
                    raise ValueError('地点字段不能为空')
            if not isinstance(p['aliases'], list) or not all(isinstance(a, str) and a for a in p['aliases']):
                raise ValueError('aliases 必须是非空字符串列表')
        self.prefix = '【示例数据】' if self.data.get('is_example', True) else ''
        self.documents = {}
        provenance = self.data.get('source', '本地医院配置（来源未填写）')
        for p in self.places.values():
            self._add({
                'id': 'place:' + p['id'], 'title': p['name'],
                'text': p['name'] + '位于' + p['location'] + '。路线说明：' + p['route'] + '。',
                'keywords': p['aliases'], 'place_id': p['id'],
                'source': p.get('source', provenance),
                'reviewed_at': p.get('reviewed_at', self.data.get('reviewed_at')),
                'expires_on': p.get('expires_on', self.data.get('expires_on')),
            })
        self._add({'id': 'flow:general', 'title': '一般就诊流程', 'text': self.data['flow'],
                   'keywords': ['挂号', '报到', '下一步', '第一次', '就诊流程'],
                   'source': provenance, 'reviewed_at': self.data.get('reviewed_at'),
                   'expires_on': self.data.get('expires_on')})
        for doc in self.data.get('documents', []):
            self._add(doc)

    def _add(self, doc):
        if not isinstance(doc, dict):
            raise ValueError('知识条目必须是对象')
        for field in ('id', 'title', 'text', 'source'):
            if not isinstance(doc.get(field), str) or not doc[field].strip():
                raise ValueError('知识条目缺少有效字段：' + field)
        if doc['id'] in self.documents:
            raise ValueError('知识条目 ID 重复：' + doc['id'])
        if doc.get('place_id') is not None and doc['place_id'] not in self.places:
            raise ValueError('知识条目引用未知地点')
        if not isinstance(doc.get('keywords', []), list) or not all(isinstance(k, str) and k for k in doc.get('keywords', [])):
            raise ValueError('keywords 必须是字符串列表')
        for field in ('reviewed_at', 'expires_on'):
            if doc.get(field): date.fromisoformat(doc[field])
        self.documents[doc['id']] = dict(doc)

    def match(self, text):
        return [p['id'] for p in self.places.values() if any(a in text for a in [p['name'], *p['aliases']])]

    def is_current(self, doc, today=None):
        return not doc.get('expires_on') or date.fromisoformat(doc['expires_on']) >= (today or date.today())

    def destination_available(self, destination):
        return destination in self.places and self.is_current(self.documents['place:' + destination])

    def search(self, query, destination=None, limit=4, today=None):
        """Chinese bigrams + keyword hits; no vector service or hidden external data."""
        if not isinstance(query, str) or not 1 <= limit <= 8:
            raise ValueError('无效检索参数')
        query_terms = terms(query)
        ranked = []
        for doc in self.documents.values():
            if not self.is_current(doc, today): continue
            corpus_terms = terms(doc['title'] + ' ' + doc['text'])
            score = len(query_terms & corpus_terms) / max(1, len(query_terms))
            score += sum(2 for word in doc.get('keywords', []) if word in query)
            if destination and doc.get('place_id') == destination: score += 5
            if score >= 0.15:
                ranked.append((score, doc['id'], doc))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return [dict(row[2]) for row in ranked[:limit]]
