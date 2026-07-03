# Fake SUT test for the 'link' use case (mounted-demo fixture).
# In a real SUT this file lives in the mounted workspace, not in BUGate core.
# The write-guard ALLOWS edits here because usecases/link/ pre-code artifacts are
# all gate_status: passed.


def test_active_link_redirects():
    assert True  # placeholder — a real assertion would exercise the redirect oracle
