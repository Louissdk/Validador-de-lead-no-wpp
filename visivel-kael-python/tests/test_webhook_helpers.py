from app.webhook_utils import extract_message, is_opt_out_message


def test_extract_evolution_message():
    data = {"event": "messages.upsert", "data": {"key": {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": "abc"}, "message": {"conversation": "Olá"}}}
    assert extract_message(data) == ("5511999999999@s.whatsapp.net", "Olá", False, "abc")


def test_ignores_payload_without_text():
    assert extract_message({"data": {"key": {"remoteJid": "5511999999999@s.whatsapp.net"}, "message": {}}}) is None


def test_opt_out_detects_multi_word_phrases_with_extra_text():
    assert is_opt_out_message("SAIR, por favor")
    assert is_opt_out_message("Não quero receber mais mensagens")
    assert is_opt_out_message("pare agora")
    assert not is_opt_out_message("não sei se quero")
