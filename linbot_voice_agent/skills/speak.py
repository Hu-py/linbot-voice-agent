"""Speech skill validates text and preserves acknowledgement semantics."""
def speak(adapter, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('无效播报文本')
    return adapter.speak(text) is True
