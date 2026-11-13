"""Conector Mercado Livre — leitura e escrita de anúncios.

Endpoints usados (verificados na documentação oficial em 06/11/2026):
  - GET  /users/me                          identifica a conta
  - GET  /users/{id}/items/search           lista IDs de anúncio (paginado)
  - GET  /items?ids=...&attributes=...      busca em lote (multiget)
  - GET  /items/{id}/description            descrição
  - GET  /categories/{id}                   limites da categoria (título, imagens)
  - GET  /items/{id}/performance            qualidade (substituiu /health em 07/02/2025)
  - GET  /visits/items?ids=...              visitas por anúncio (lote)
  - PUT  /items/{id}                        escrita (só após aprovação humana)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Any, Iterable
from urllib.parse import urlencode

from hub.connectors.base import Connector
from hub.connectors.http import RateLimitedClient
from hub.core.models import Attribute, Image, Listing, ListingKey, Quality

log = logging.getLogger(__name__)

API = "https://api.mercadolibre.com"
AUTH = "https://auth.mercadolivre.com.br/authorization"
TOKEN = "https://api.mercadolibre.com/oauth/token"

# offline_access é o que garante refresh_token; read/write cobrem diagnóstico e
# (fase futura) aplicação de correções. Sem offline_access a conta cai a cada 6 h.
SCOPE = "offline_access read write"

# Campos pedidos no multiget. Pedir uma lista curta faz o ML OMITIR o resto da
# resposta — era por isso que condição, garantia, tipo de anúncio e dimensões
# nunca chegavam ao banco.
CAMPOS_ITEM = ",".join([
    "id", "title", "category_id", "price", "base_price", "original_price",
    "currency_id", "available_quantity", "initial_quantity", "sold_quantity",
    "status", "sub_status", "condition", "listing_type_id", "buying_mode",
    "pictures", "attributes", "variations", "shipping", "warranty",
    "sale_terms", "official_store_id", "catalog_listing", "catalog_product_id",
    "channels", "accepts_mercadopago", "automatic_relist", "domain_id",
    "family_id", "family_name", "user_product_id", "parent_item_id",
    "permalink", "date_created", "video_id", "videos",
    "last_updated", "expiration_time", "end_time", "seller_custom_field",
    "inventory_id", "international_delivery_mode",
])


# ----------------------------------------------------------------------
# Tradução: payload do Mercado Livre -> modelo canônico
# Função pura, testável sem rede. É aqui que se concentra a "lingua" do ML.
# ----------------------------------------------------------------------
def translate_item(item: dict[str, Any], *, platform: str, account: str,
                   description: str = "", category: dict[str, Any] | None = None,
                   performance: dict[str, Any] | None = None) -> Listing:
    category = category or {}
    settings = category.get("settings") or {}

    images = []
    for pic in item.get("pictures") or []:
        width = pic.get("width") or (settings.get("_pic_w") if settings else None)
        height = pic.get("height")
        if not (width and height):
            size = pic.get("max_size") or pic.get("size") or ""
            # formato comum: "1200x900"
            if "x" in str(size):
                try:
                    w, h = str(size).lower().split("x")
                    width, height = int(w), int(h)
                except ValueError:
                    pass
        images.append(Image(url=pic.get("secure_url") or pic.get("url") or "",
                            width=width, height=height))

    attributes = []
    for a in item.get("attributes") or []:
        tags = a.get("tags") or {}
        values = a.get("values") or []
        value = a.get("value_name")
        if value in (None, "") and values:
            value = [v.get("name") for v in values if v.get("name")]
        attributes.append(Attribute(
            id=a.get("id") or "",
            name=a.get("name") or "",
            value=value,
            required=bool(tags.get("required")),
        ))

    shipping = item.get("shipping") or {}
    quality = Quality()
    if performance:
        quality = Quality(
            score=performance.get("score"),
            source="native",
            level=performance.get("level"),
            pending_actions=performance.get("buckets") or performance.get("actions") or [],
        )

    return Listing(
        key=ListingKey(platform=platform, account=account, item_id=str(item.get("id"))),
        status=item.get("status") or "unknown",
        title=item.get("title") or "",
        description=description or "",
        category_id=item.get("category_id") or "",
        category_path=[c.get("name", "") for c in (category.get("path_from_root") or [])],
        attributes=attributes,
        images=images,
        price_amount=item.get("price"),
        currency=item.get("currency_id") or "BRL",
        stock=item.get("available_quantity"),
        free_shipping=shipping.get("free_shipping"),
        quality=quality,
        category_limits={
            "max_title_length": settings.get("max_title_length"),
            "max_pictures": settings.get("max_pictures_per_item"),
            "max_description_length": settings.get("max_description_length"),
        },
        sold_quantity=int(item.get("sold_quantity") or 0),
        catalog_listing=bool(item.get("catalog_listing")),
        catalog_product_id=item.get("catalog_product_id"),
        condition=item.get("condition"),
        listing_type_id=item.get("listing_type_id"),
        buying_mode=item.get("buying_mode"),
        warranty=item.get("warranty"),
        sale_terms=item.get("sale_terms") or [],
        shipping=shipping,
        dimensions=(shipping.get("dimensions") or {}),
        official_store_id=item.get("official_store_id"),
        channels=item.get("channels") or [],
        accepts_mercadopago=item.get("accepts_mercadopago"),
        automatic_relist=item.get("automatic_relist"),
        base_price=item.get("base_price"),
        original_price=item.get("original_price"),
        initial_quantity=item.get("initial_quantity"),
        domain_id=item.get("domain_id"),
        family_id=item.get("family_id"),
        family_name=item.get("family_name"),
        user_product_id=item.get("user_product_id"),
        parent_item_id=item.get("parent_item_id"),
        permalink=item.get("permalink"),
        video_id=item.get("video_id"),
        videos=item.get("videos") or [],
        date_created=item.get("date_created"),
        expiration_time=item.get("expiration_time"),
        end_time=item.get("end_time"),
        seller_custom_field=item.get("seller_custom_field"),
        inventory_id=item.get("inventory_id"),
        international_delivery_mode=item.get("international_delivery_mode"),
        sub_status=item.get("sub_status") or [],
        variations=item.get("variations") or [],
        raw=item,
        updated_at_platform=item.get("last_updated"),
    )


class MercadoLivreConnector(Connector):
    platform = "mercadolivre"

    def __init__(self, account: str, client_id: str, client_secret: str,
                 access_token: str | None = None, refresh_token: str | None = None,
                 redirect_uri: str | None = None):
        self.account = account
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.http = RateLimitedClient(API)
        self._category_cache: dict[str, dict] = {}
        self._me: dict | None = None

    # --- autenticação ---------------------------------------------------
    @staticmethod
    def novo_code_verifier() -> str:
        """Verificador PKCE: 43-128 caracteres de [A-Za-z0-9-._~] (RFC 7636).

        token_urlsafe(64) produz 86 caracteres nesse alfabeto — dentro da faixa.
        """
        return secrets.token_urlsafe(64)

    @staticmethod
    def code_challenge(verifier: str) -> str:
        """S256: BASE64URL(SHA256(verifier)) sem padding, como o ML exige."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def authorize_url(self, state: str, scope: str = SCOPE,
                      code_verifier: str | None = None) -> str:
        """URL de autorização.

        O parâmetro `scope` é OBRIGATÓRIO para receber refresh_token: sem
        `offline_access` o Mercado Livre devolve só o access_token (que vale 6 h)
        e nunca um refresh — a conta cai a cada 6 horas e só volta com
        autorização manual.
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri or "",
            "state": state,
            "scope": scope,
        }
        # PKCE: se o app do ML tiver PKCE habilitado, enviar isto é OBRIGATÓRIO.
        # Se não estiver habilitado, o ML ignora — então enviar sempre é seguro.
        if code_verifier:
            params["code_challenge"] = self.code_challenge(code_verifier)
            params["code_challenge_method"] = "S256"
        return f"{AUTH}?{urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri or "",
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        resp = self.http.session.post(TOKEN, data=payload,
            headers={"Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30)
        if resp.status_code >= 400:
            # raise_for_status() joga fora o corpo, e é no corpo que o ML diz
            # POR QUE recusou. Sem isso o diagnóstico vira adivinhação.
            raise RuntimeError(
                f"O Mercado Livre recusou a troca do token ({resp.status_code}): "
                f"{resp.text[:400]}")
        data = resp.json()
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        return data

    def refresh(self) -> dict[str, Any]:
        if not self.refresh_token:
            raise RuntimeError("Sem refresh_token: reconecte a conta pelo link de autorização.")
        resp = self.http.session.post(TOKEN, data={
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data.get("access_token")
        # O ML pode rotacionar o refresh token: guardar sempre o mais novo.
        self.refresh_token = data.get("refresh_token") or self.refresh_token
        return data

    # --- leitura --------------------------------------------------------
    def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = self.http.request("GET", "/users/me", token=self.access_token)
        return self._me or {}

    def list_item_ids(self) -> Iterable[str]:
        user_id = self.me().get("id")
        if not user_id:
            raise RuntimeError("Não foi possível identificar a conta (GET /users/me).")
        scroll_id = None
        while True:
            params = {"search_type": "scan", "limit": 100}
            if scroll_id:
                params["scroll_id"] = scroll_id
            data = self.http.request("GET", f"/users/{user_id}/items/search",
                                     token=self.access_token, params=params)
            for item_id in data.get("results") or []:
                yield item_id
            scroll_id = data.get("scroll_id")
            if not scroll_id:
                break

    def visits_by_items(self, item_ids: list[str]) -> dict[str, int]:
        """Visitas totais por anúncio (últimos 2 anos), em lote.

        Endpoint: `GET /visits/items?ids=MLB1,MLB2...`. Devolve {item_id: total}.

        Por que em lote: chamar um a um para 8.570 anúncios seriam 8.570
        requisições. Aqui são ~86 lotes de 100 — viável numa janela noturna.

        Visita é o dado que separa "ninguém viu" de "viu e não comprou" —
        diagnóstico que a conformidade do marketplace não dá.
        """
        saida: dict[str, int] = {}
        for i in range(0, len(item_ids), 100):
            chunk = item_ids[i:i + 100]
            try:
                data = self.http.request(
                    "GET", "/visits/items", token=self.access_token,
                    params={"ids": ",".join(chunk)})
            except Exception as exc:  # noqa: BLE001
                log.warning("visitas falharam para lote %d: %s", i // 100, exc)
                continue
            if isinstance(data, list):
                for linha in data:
                    if isinstance(linha, dict) and linha.get("id"):
                        saida[str(linha["id"])] = int(
                            linha.get("total_visits") or 0)
            elif isinstance(data, dict):
                for k, v in data.items():
                    try:
                        saida[str(k)] = int(v)
                    except (TypeError, ValueError):
                        continue
        return saida

    def category(self, category_id: str) -> dict[str, Any]:
        if category_id not in self._category_cache:
            self._category_cache[category_id] = self.http.request(
                "GET", f"/categories/{category_id}", token=self.access_token) or {}
        return self._category_cache[category_id]

    def description(self, item_id: str) -> str:
        try:
            data = self.http.request("GET", f"/items/{item_id}/description",
                                     token=self.access_token)
            return (data or {}).get("plain_text") or ""
        except Exception:  # descrição ausente não deve derrubar a coleta
            log.warning("Sem descrição para %s", item_id)
            return ""

    def performance(self, item_id: str) -> dict[str, Any] | None:
        try:
            return self.http.request("GET", f"/items/{item_id}/performance",
                                     token=self.access_token)
        except Exception:
            log.warning("Sem /performance para %s", item_id)
            return None

    def fetch_listings(self, item_ids: list[str]) -> list[Listing]:
        out: list[Listing] = []
        for start in range(0, len(item_ids), 20):  # multiget do ML: até 20 ids
            chunk = item_ids[start:start + 20]
            data = self.http.request(
                "GET", "/items",
                token=self.access_token,
                params={"ids": ",".join(chunk), "attributes": CAMPOS_ITEM},
            ) or []
            for entry in data:
                item = (entry or {}).get("body") or {}
                if not item.get("id"):
                    continue
                item_id = str(item["id"])
                cat = self.category(item.get("category_id") or "") if item.get("category_id") else {}
                out.append(translate_item(
                    item,
                    platform=self.platform,
                    account=self.account,
                    description=self.description(item_id),
                    category=cat,
                    performance=self.performance(item_id),
                ))
            log.info("Coletados %s/%s anúncios", min(start + 20, len(item_ids)), len(item_ids))
        return out

    def itens_da_familia(self, family_id: int, seller_id: str | int,
                         limite: int = 40) -> list[dict]:
        """Itens da mesma família (User Product).

        `GET /items` de um item traz `family_id`; para achar os irmãos, a
        documentação de User Products manda buscar por `user_product_id`. Como
        o item pode não ter esse campo no payload coletado, o caminho confiável
        aqui é varrer a lista do vendedor filtrando por family_id.

        Devolve o mínimo para decidir quem é o pai: item_id, parent_item_id.
        """
        saida: list[dict] = []
        try:
            ids = list(self.list_item_ids())
        except Exception:  # noqa: BLE001
            return saida
        for start in range(0, len(ids), 20):
            chunk = ids[start:start + 20]
            try:
                data = self.http.request(
                    "GET", "/items", token=self.access_token,
                    params={"ids": ",".join(chunk),
                            "attributes": "id,family_id,parent_item_id,sold_quantity"})
            except Exception:  # noqa: BLE001
                continue
            for entry in data or []:
                b = (entry or {}).get("body") or {}
                if not b.get("id"):
                    continue
                if str(b.get("family_id")) == str(family_id):
                    saida.append({"item_id": str(b["id"]),
                                  "parent_item_id": b.get("parent_item_id"),
                                  "sold_quantity": int(b.get("sold_quantity") or 0),
                                  "family_id": b.get("family_id")})
                    if len(saida) >= limite:
                        return saida
        return saida

    # --- escrita --------------------------------------------------------
    def update_listing(self, item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Grava o anúncio usando o endpoint CORRETO para cada campo.

        Aprendido em produção (08/11/2026): mandar `description` dentro do
        PUT /items devolve 400 `item.description.not_modifiable` em anúncios do
        modelo User Products. A descrição tem endpoint próprio.

        Se a descrição falhar, o resto ainda é tentado — um campo recusado não
        pode derrubar os outros.
        """
        corpo = dict(patch or {})
        descricao = corpo.pop("description", None)
        resultado: dict[str, Any] = {}

        if descricao is not None:
            try:
                resultado["description"] = self.update_description(item_id, descricao)
            except Exception as exc:  # noqa: BLE001
                resultado["description_error"] = str(exc)[:300]

        if corpo:
            try:
                resultado["item"] = self.http.request(
                    "PUT", f"/items/{item_id}", token=self.access_token,
                    json_body=corpo) or {}
            except Exception as exc:  # noqa: BLE001
                resultado["item_error"] = str(exc)[:300]

        if resultado.get("description_error") and resultado.get("item_error"):
            raise RuntimeError(
                "Nenhum campo pôde ser gravado: "
                f"descrição: {resultado['description_error']} | "
                f"item: {resultado['item_error']}")
        return resultado

    def update_description(self, item_id: str, plain_text: str) -> dict[str, Any]:
        """`PUT /items/{id}/description` — o caminho da descrição."""
        return self.http.request(
            "PUT", f"/items/{item_id}/description", token=self.access_token,
            json_body={"plain_text": plain_text}) or {}


def from_env(account: str) -> MercadoLivreConnector:
    """Monta o conector a partir de variáveis de ambiente. Nunca hardcode segredo."""
    return MercadoLivreConnector(
        account=account,
        client_id=os.environ["ML_CLIENT_ID"],
        client_secret=os.environ["ML_CLIENT_SECRET"],
        redirect_uri=os.environ.get("ML_REDIRECT_URI"),
    )
