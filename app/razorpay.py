import hashlib,hmac
from datetime import datetime, timedelta, timezone
import httpx
from .config import get_settings

class RazorpayError(RuntimeError): pass
class RazorpayTestAdapter:
    """Safe Test-Mode adapter. Payment Links are real Razorpay test resources; no live charge is attempted."""
    def __init__(self):
        s=get_settings(); self.base='https://api.razorpay.com/v1'; self.auth=(s.razorpay_key_id,s.razorpay_key_secret)
    def enabled(self): return bool(self.auth[0] and self.auth[1])
    def create_payment_link(self, case, reference_id, notify=False):
        if not self.enabled(): raise RazorpayError('Razorpay test credentials are not configured')
        amount=max(100,int(round(float(case['amount_inr'])*100)))
        payload={'amount':amount,'currency':'INR','accept_partial':False,'description':f"CRRO recovery for {case['case_id']}",'reference_id':reference_id,'reminder_enable':False,'notify':{'sms':bool(notify),'email':False},'notes':{'case_id':str(case['case_id']),'crro':'true'}}
        r=httpx.post(self.base+'/payment_links',auth=self.auth,json=payload,timeout=20); r.raise_for_status(); return r.json()
    def fetch_payment_link(self, link_id):
        r=httpx.get(self.base+f'/payment_links/{link_id}',auth=self.auth,timeout=20); r.raise_for_status(); return r.json()

def verify_webhook(raw_body,signature,secret):
    if not secret or not signature: return False
    digest=hmac.new(secret.encode(),raw_body,hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest,signature)
