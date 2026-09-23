"""Deterministic Russian/English routing; unrecognized questions do no work."""

import re

from .contracts import MAX_ITEMS, Intent, Request, RequestError


def route(question: str) -> Intent:
    q = question.casefold().replace("ё", "е")
    if re.search(r"общ\w* (сборщик|получател)|кто собирает|собира\w* деньги с|common collector", q):
        return "common_collector"
    if re.search(r"откуда|куда.*(деньги|ушли)|upstream|downstream", q):
        return "trace"
    if re.search(r"следующ\w* шаг|дальше провер|провер\w* дальше|next (step|investigation)|кого.*(перв|провер)|who.*(first|investigate)", q):
        return "next_step"
    if re.search(r"почему|объясни|объяснение|роль|приоритет|конечн\w* получател|why|explain|priority", q):
        return "explanation"
    return "other"


def plan(request: Request, intent: Intent) -> list[dict]:
    gids = list(request.selected_gids)

    def call(tool, **args):
        return {"tool": tool, "args": args}

    cards = [call("get_node", gid=g) for g in gids]
    if intent == "other":
        return []
    if intent == "common_collector":
        if len(gids) < 2:
            raise RequestError("Для общего сборщика выберите минимум два gid")
        return [call("compare_nodes", gids=gids),
                call("find_common_collectors", gids=gids, depth=request.depth, limit=MAX_ITEMS)]
    if intent == "explanation":
        if not gids:
            raise RequestError("Выберите gid, для которого нужно объяснить роль и приоритет")
        return cards
    if intent == "trace":
        if len(gids) != 1:
            raise RequestError("Для обхода выберите один gid")
        q = request.question.casefold()
        upstream = bool(re.search(r"откуда|upstream", q))
        downstream = bool(re.search(r"куда.*(ушли|дальше)|downstream", q)) or not upstream
        calls = cards
        if upstream:
            calls += [call("get_incoming", gid=gids[0], limit=MAX_ITEMS),
                      call("trace_upstream", gids=gids, depth=request.depth, limit=MAX_ITEMS)]
        if downstream:
            calls += [call("get_outgoing", gid=gids[0], limit=MAX_ITEMS),
                      call("trace_downstream", gid=gids[0], depth=request.depth, limit=MAX_ITEMS)]
        return calls
    if not gids:
        return [call("rank_candidates", limit=MAX_ITEMS)]
    # Primary selection is the first gid. Explain every selection, investigate
    # incoming/outgoing for the primary one. No unrequested global search.
    return cards + [call("get_incoming", gid=gids[0], limit=MAX_ITEMS),
                    call("get_outgoing", gid=gids[0], limit=MAX_ITEMS)]
