@app.post("/painel/api/visitas/coletar", dependencies=[Depends(rate_limit), Depends(auth)])
def painel_coletar_visitas(body: dict, alias: str = Query(default="")) -> dict:
    """Coleta visitas por anúncio. Retomável: só pega quem falta hoje."""
    limite = body.get("limite")
    try:
        limite = int(limite) if limite else None
    except (TypeError, ValueError):
        raise HTTPException(422, "limite deve ser número")
    try:
        return get_service().coletar_visitas(
            alias or _ALIAS, limite,
            bool(body.get("somente_sem_dado", True)))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/painel/api/visitas/resumo", dependencies=[Depends(rate_limit), Depends(auth)])
def painel_resumo_trafego(alias: str = Query(default=""),
                          limite: int = Query(default=20, ge=1, le=100)) -> dict:
    """Quem tem tráfego, quem não tem, e quem visita e não compra."""
    try:
        return get_service().resumo_trafego(alias or _ALIAS, limite)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
