import hashlib,hmac

def test_webhook_signature():
    from app.razorpay import verify_webhook
    body=b'{"event":"payment_link.paid"}'; secret='s'; sig=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    assert verify_webhook(body,sig,secret)
    assert not verify_webhook(body,'bad',secret)

def test_analyst_allowlist():
    from app.analyst import RecoveryAnalyst
    out=RecoveryAnalyst().analyze({'case_id':'R-1','failure_reason':'bank_decline','attempt_count':1},[],['NO_ACTION','RETRY_NOW'])
    assert out.recommended_action in {'NO_ACTION','RETRY_NOW'}

def test_retrieval():
    from app.retrieval import VamanaIndex
    idx=VamanaIndex(); idx.add(1,'insufficient funds payment failed'); idx.add(2,'network timeout retry later')
    assert idx.search('funds payment',1)[0].id==1
