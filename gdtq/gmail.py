"""Gmail sender/drafter with explicit authorization and RFC-compliant MIME messages."""
from __future__ import annotations
import base64,json,urllib.request
from email.message import EmailMessage
from email.utils import parseaddr
from application_service.google_auth import access_token_value

class GmailActionError(RuntimeError):pass

def validated_email(value):
    name,address=parseaddr(value or "")
    if not address or "@" not in address or "\n" in value or "\r" in value:
        raise ValueError("Invalid email recipient")
    return address

class GmailActions:
    def __init__(self,access_token,*,allow_send=False,opener=None):
        self.access_token,self.allow_send=access_token,bool(allow_send)
        self.opener=opener or urllib.request.urlopen

    def _post(self,path,payload):
        token=access_token_value(self.access_token)
        if not token:raise GmailActionError("Missing Gmail OAuth credentials")
        request=urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me"+path,
            data=json.dumps(payload).encode("utf-8"),method="POST",
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        with self.opener(request,timeout=25) as result:
            return json.loads(result.read().decode("utf-8"))

    def compose(self,recipient,subject,body,thread_id=None,*,send=False,
                in_reply_to=None,reference=None,attachment=None):
        recipient=validated_email(recipient)
        if not subject.strip() or not body.strip():raise ValueError("Subject and message required")
        msg=EmailMessage()
        msg["To"]=recipient
        msg["Subject"]=subject.replace("\n"," ").replace("\r"," ")
        if in_reply_to:msg["In-Reply-To"]=in_reply_to
        if reference:msg["References"]=reference
        msg.set_content(body)
        if attachment:
            filename,content=attachment
            msg.add_attachment(content,maintype="application",subtype="pdf",filename=filename)
        encoded=base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        message={"raw":encoded}
        if thread_id:message["threadId"]=thread_id
        if send:
            if not self.allow_send:raise PermissionError("Automatic Gmail send not authorized")
            return self._post("/messages/send",message)
        return self._post("/drafts",{"message":message})
