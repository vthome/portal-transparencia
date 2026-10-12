import requests

from tests.helpers import FakeSite, make_http, test_settings

BASE = "https://www.exemplo.rj.gov.br"


def test_get_ok_e_user_agent_identificado():
    site = FakeSite().html(f"{BASE}/", "<html>ok</html>")
    seen = []
    site.handler(f"{BASE}/ua", lambda req: (seen.append(req.headers["User-Agent"]) or (200, {}, b"x")))
    http = make_http(site)
    r = http.fetch(f"{BASE}/")
    assert r.ok and r.status == 200 and r.content == b"<html>ok</html>" and r.erro is None
    http.fetch(f"{BASE}/ua")
    assert seen == ["MonitorTransparenciaBot/test"]


def test_robots_bloqueia_e_registra():
    site = FakeSite().robots(BASE, "User-agent: *\nDisallow: /portal/").html(f"{BASE}/portal/x", "segredo")
    http = make_http(site)
    r = http.fetch(f"{BASE}/portal/x")
    assert r.erro == "bloqueado_robots" and r.content == b""
    assert f"{BASE}/portal/x" not in site.calls          # nem chegou a pedir a página


def test_robots_override_coleta_e_marca():
    site = FakeSite().robots(BASE, "User-agent: *\nDisallow: /").html(f"{BASE}/portal/x", "conteudo")
    r = make_http(site).fetch(f"{BASE}/portal/x", robots_override=True)
    assert r.ok and r.robots_override is True


def test_robots_ausente_permite():
    site = FakeSite().html(f"{BASE}/a", "ok")   # /robots.txt cai no 404 padrão
    assert make_http(site).fetch(f"{BASE}/a").ok


def test_retry_em_503_com_backoff():
    sleeps = []
    site = FakeSite().add(f"{BASE}/x", status=503, times=2).html(f"{BASE}/x", "voltou")
    r = make_http(site, sleeps=sleeps).fetch(f"{BASE}/x")
    assert r.ok and r.tentativas == 3
    assert [s for s in sleeps if s >= 1] == [1, 2]     # backoff exponencial 1s, 2s


def test_retry_after_respeitado():
    sleeps = []
    site = FakeSite().add(f"{BASE}/x", status=429, headers={"Retry-After": "7"}, times=1).html(f"{BASE}/x", "ok")
    make_http(site, sleeps=sleeps).fetch(f"{BASE}/x")
    assert 7.0 in sleeps


def test_404_nao_e_retentado():
    site = FakeSite()
    r = make_http(site).fetch(f"{BASE}/nao-existe")
    assert r.erro == "http_404" and r.tentativas == 1


def test_timeout_classificado():
    site = FakeSite().handler(f"{BASE}/lento", lambda req: requests.exceptions.ReadTimeout("lento"))
    r = make_http(site, test_settings(tentativas=2)).fetch(f"{BASE}/lento")
    assert r.erro == "timeout" and r.tentativas == 2 and not r.ok


def test_dns_classificado():
    site = FakeSite().handler(f"{BASE}/x", lambda req: requests.exceptions.ConnectionError(
        "Failed to resolve: Name or service not known"))
    r = make_http(site, test_settings(tentativas=1)).fetch(f"{BASE}/x")
    assert r.erro == "dns"


def test_teto_de_bytes():
    site = FakeSite().add(f"{BASE}/grande.pdf", body=b"x" * 5000, headers={"content-type": "application/pdf"})
    r = make_http(site).fetch(f"{BASE}/grande.pdf", max_bytes=1000)
    assert r.erro == "muito_grande" and r.truncado and len(r.content) <= 1000


def test_content_length_declarado_acima_do_teto_nem_baixa():
    site = FakeSite().add(f"{BASE}/g.pdf", body=b"x" * 10, headers={"content-length": "999999999"})
    r = make_http(site).fetch(f"{BASE}/g.pdf", max_bytes=1000)
    assert r.erro == "muito_grande" and r.content == b""


def test_get_condicional_304():
    def h(req):
        assert req.headers.get("If-None-Match") == '"abc"'
        return 304, {}, b""
    site = FakeSite().handler(f"{BASE}/rreo.pdf", h)
    r = make_http(site).fetch(f"{BASE}/rreo.pdf", etag='"abc"')
    assert r.nao_modificado and r.erro is None and not r.ok


def test_circuit_breaker_abre_apos_falhas_seguidas():
    site = FakeSite()
    for i in range(10):
        site.add(f"{BASE}/p{i}", status=500)
    http = make_http(site, test_settings(tentativas=1))
    http._limite_circuito = 3
    erros = [http.fetch(f"{BASE}/p{i}").erro for i in range(5)]
    assert erros[:3] == ["http_500"] * 3 and erros[3:] == ["circuito_aberto"] * 2


def test_throttle_por_dominio():
    """Com 1 req/s, a 2ª requisição ao mesmo domínio espera; outro domínio não."""
    sleeps = []
    t = [0.0]
    site = FakeSite().html(f"{BASE}/a", "a").html(f"{BASE}/b", "b").html("https://outro.gov.br/c", "c")
    import requests as rq

    from ingestion.extract.http_client import HttpClient

    s = rq.Session()
    s.mount("https://", site)
    http = HttpClient(test_settings(req_por_segundo=1), session=s, sleep=sleeps.append, clock=lambda: t[0])
    http.fetch(f"{BASE}/a")            # robots.txt + página consomem slots do domínio
    http.fetch("https://outro.gov.br/c")
    esperas_mesmo_dominio = [x for x in sleeps if x > 0]
    assert esperas_mesmo_dominio, "deveria ter esperado entre robots.txt e a página no mesmo domínio"
    assert all(abs(x - 1.0) < 1e-6 for x in esperas_mesmo_dominio)
