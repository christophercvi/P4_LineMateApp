"""Model registry. The list comes from the local Ollama server every time (cached ~20 s), so any model
pulled with `ollama pull <name>` shows up in the Ask and Triage dropdowns without a code change.
An admin can hide models per role (stored as a block list)."""

import os
from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import CHAT_ROLES, Actor, can
from back_end.core.errors import BadRequest, Forbidden, NotFound, UpstreamUnavailable
from back_end.db import models as m
from back_end.schemas import AdminModelsOut, AllowlistIn, ApiModel, ModelLimits, ModelOut, MyModelsOut
from back_end.services import repo
from back_end.services.container import Services
from back_end.services.ollama import ModelInfo

router = APIRouter(prefix="/api", tags=["models"])


def model_out(i: ModelInfo) -> ModelOut:
    return ModelOut(
        name=i.name,
        family=i.family,
        parameters=i.parameters,
        quantization=i.quantization,
        size_gb=i.size_gb,
        context_length=i.context_length,
        capabilities=i.capabilities,
        thinking=i.thinking,
        loaded=i.loaded,
        keep_alive=i.keep_alive,
        purpose=i.purpose,
        supported=i.supported,
    )


async def _all(svc: Services) -> tuple[list[ModelInfo], bool]:
    try:
        return await svc.ollama.list_models(), True
    except Exception:  # noqa: BLE001 - Ollama not running: the UI shows a banner instead of failing
        return [], False


async def _blocked(svc: Services) -> dict[str, list[str]]:
    async with svc.db.session() as s:
        rows = await repo.all_rows(s, m.ModelAllowlist)
    return {r.role: list(r.blocked or []) for r in rows}


async def models_for(svc: Services, actor: Actor) -> tuple[list[ModelInfo], bool]:
    infos, up = await _all(svc)
    blocked = set((await _blocked(svc)).get(actor.role, []))
    return [i for i in infos if not i.embedding and "completion" in i.capabilities and i.name not in blocked], up


@router.get("/models", response_model=MyModelsOut, summary="Chat models the caller may use")
async def my_models(actor: ActorDep, svc: SvcDep) -> MyModelsOut:
    mine, up = await models_for(svc, actor)
    preferred = next((i.name for i in mine if i.name == svc.settings.chat_model), None)
    default = preferred or next((i.name for i in mine if i.supported), None) or (mine[0].name if mine else None)
    return MyModelsOut(models=[model_out(i) for i in mine], default=default, vision=svc.vision.enabled, ollama_up=up)


def _admin(actor: Actor) -> None:
    if not can.admin(actor):
        raise Forbidden("Model management is for Admins.")


def _host_memory_gb() -> float:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        return 0.0


@router.get("/admin/models", response_model=AdminModelsOut, summary="All local models, role access and memory")
async def admin_models(actor: ActorDep, svc: SvcDep) -> AdminModelsOut:
    _admin(actor)
    infos, up = await _all(svc)
    chat = [i.name for i in infos if not i.embedding and "completion" in i.capabilities]
    blocked = await _blocked(svc)
    allow = {role: [n for n in chat if n not in set(blocked.get(role, []))] for role in CHAT_ROLES}
    loaded = [i for i in infos if i.loaded]
    return AdminModelsOut(
        models=[model_out(i) for i in infos],
        allowlist=allow,
        limits=ModelLimits(
            max_loaded_models=int(os.environ.get("OLLAMA_MAX_LOADED_MODELS", "0") or 0),
            num_parallel=int(os.environ.get("OLLAMA_NUM_PARALLEL", "0") or 0),
            host_memory_gb=_host_memory_gb(),
            reserved_gb=2.0,
            loaded_gb=round(sum(i.size_gb for i in loaded), 2),
            loaded_count=len(loaded),
        ),
        ollama_up=up,
    )


@router.put("/admin/models/allowlist", response_model=AdminModelsOut, summary="Choose which models each role sees")
async def put_allowlist(body: AllowlistIn, actor: ActorDep, svc: SvcDep) -> AdminModelsOut:
    _admin(actor)
    infos, up = await _all(svc)
    if not up:
        raise UpstreamUnavailable("Ollama is not reachable, so the model list cannot be saved right now.")
    chat = [i.name for i in infos if not i.embedding and "completion" in i.capabilities]
    async with svc.db.session() as s:
        for role in CHAT_ROLES:
            allowed = set(body.allowlist.get(role, chat))
            if not allowed & set(chat):
                raise BadRequest(f"Leave at least one model for the {role.replace('_', ' ')} role.")
            row = await s.get(m.ModelAllowlist, role) or m.ModelAllowlist(role=role)
            row.blocked = sorted(n for n in chat if n not in allowed)
            s.add(row)
    return await admin_models(actor, svc)


class ModelOpIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/admin/models/{op}", response_model=ModelOut, summary="Load (warm) or unload a model")
async def model_op(op: Literal["load", "unload"], body: ModelOpIn, actor: ActorDep, svc: SvcDep) -> ModelOut:
    _admin(actor)
    info = await svc.ollama.get(body.name)
    if info is None:
        raise NotFound(f"Model {body.name}")
    try:
        if op == "load":
            await svc.ollama.ensure_loaded(info.name)
        else:
            await svc.ollama.unload(info.name)
    except Exception as exc:
        raise UpstreamUnavailable(f"Ollama could not {op} {info.name}: {exc}") from exc
    fresh = await svc.ollama.get(info.name)
    return model_out(fresh or info)
