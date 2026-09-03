from dataclasses import dataclass
import math, re
from collections import Counter

@dataclass
class Hit:
    id:int; title:str; text:str; score:float; metadata:dict

def tokens(s): return re.findall(r'[a-z0-9_]+', s.lower())

class VamanaIndex:
    """Small dependency-free Vamana-inspired graph index.
    It keeps a bounded neighbor graph and performs greedy best-first traversal.
    The interface is intentionally replaceable by a native Vamana/FAISS service.
    """
    def __init__(self, M=8): self.M=M; self.items={}; self.graph={}
    def _vec(self,s):
        c=Counter(tokens(s)); n=math.sqrt(sum(v*v for v in c.values())) or 1
        return {k:v/n for k,v in c.items()}
    def _sim(self,a,b): return sum(v*b.get(k,0) for k,v in a.items())
    def add(self,item_id,text,metadata=None):
        v=self._vec(text); self.items[item_id]=(v,text,metadata or {})
        if len(self.items)==1: self.graph[item_id]=[]; return
        scored=sorted(((self._sim(v,x[0]),i) for i,x in self.items.items() if i!=item_id),reverse=True)[:self.M]
        self.graph[item_id]=[i for _,i in scored]
        for _,i in scored: self.graph.setdefault(i,[]).append(item_id); self.graph[i]=self.graph[i][-self.M:]
    def search(self,query,k=5):
        if not self.items:return []
        q=self._vec(query); start=next(iter(self.items)); seen={start}; frontier=[start]
        for _ in range(min(32,max(1,len(self.items)))):
            candidates=sorted(((self._sim(q,self.items[i][0]),i) for i in frontier),reverse=True)
            if not candidates: break
            _,best=candidates[0]; nxt=[i for i in self.graph.get(best,[]) if i not in seen]
            if not nxt: break
            for i in nxt: seen.add(i); frontier.append(i)
        ranked=sorted(((self._sim(q,x[0]),i,x) for i,x in self.items.items()),reverse=True)[:k]
        return [Hit(i,x[1],x[1],float(score),x[2]) for score,i,x in ranked]

class EvidenceRetriever:
    def __init__(self, repo): self.repo=repo
    def index(self,merchant_id):
        idx=VamanaIndex()
        for e in self.repo.evidence(merchant_id): idx.add(e.id,e.title+' '+e.text,e.metadata_json)
        return idx
    def search(self,merchant_id,query,k=5): return self.index(merchant_id).search(query,k)
