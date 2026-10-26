from app.browser_assist import eligibility_problem


def good_lead():
    return {
        "active": True,
        "consent_status": "opt_in",
        "consent_source": "formulario",
        "consent_at": "2026-10-01T10:00:00-03:00",
        "replied_at": None,
    }


def test_authorized_lead_can_prepare_manual_draft():
    assert eligibility_problem(good_lead(), suppressed=False) is None


def test_unknown_consent_is_blocked():
    lead = good_lead()
    lead["consent_status"] = "unknown"
    assert "opt-in" in eligibility_problem(lead)


def test_missing_consent_evidence_is_blocked():
    lead = good_lead()
    lead["consent_at"] = None
    assert "origem e data" in eligibility_problem(lead)


def test_replied_or_opted_out_contact_is_blocked():
    lead = good_lead()
    lead["replied_at"] = "already-replied"
    assert "já respondeu" in eligibility_problem(lead)
    assert "supressão" in eligibility_problem(good_lead(), suppressed=True)
