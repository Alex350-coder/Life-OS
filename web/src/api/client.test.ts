import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, installFakeApi, ok } from "../test/fakeApi";
import { ApiError, checkSession, get, post, postVoid } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("cliente de la API", () => {
  it("devuelve los datos del sobre", async () => {
    installFakeApi({ "GET /api/tareas": ok([{ id: "t-aaaaaa" }]) });
    await expect(get("/api/tareas")).resolves.toEqual([{ id: "t-aaaaaa" }]);
  });

  it("omite los parámetros vacíos de la consulta", async () => {
    const calls = installFakeApi({ "GET /api/tareas": ok([]) });
    await get("/api/tareas", { area: "", texto: "sql", archivadas: false });
    expect(calls[0]?.url).toBe("/api/tareas?texto=sql&archivadas=false");
  });

  it("envía el token CSRF en cada escritura y lo pide una sola vez", async () => {
    const calls = installFakeApi({ "POST /api/capturas": { status: 201, body: { ok: true, data: { path: "00-Inbox/x.md" } } } });
    await post("/api/capturas", { texto: "a" });
    await post("/api/capturas", { texto: "b" });
    const writes = calls.filter((call) => call.method === "POST");
    expect(writes.map((call) => call.headers["X-CSRF-Token"])).toEqual(["csrf-de-prueba", "csrf-de-prueba"]);
    expect(calls.filter((call) => call.url === "/api/session")).toHaveLength(1);
  });

  it("convierte un error de la API en ApiError con código y datos", async () => {
    installFakeApi({ "POST /api/plan/aprobacion": fail(422, "plan_rechazado", "1 conflicto(s)", { accepted: false }) });
    const error = await post("/api/plan/aprobacion", {}).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "plan_rechazado", data: { accepted: false } });
  });

  it("trata una respuesta que no es JSON como error legible", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 502 })));
    await expect(get("/api/hoy")).rejects.toMatchObject({ code: "respuesta", status: 502 });
  });

  it("sabe si hay sesión", async () => {
    installFakeApi({ "GET /api/session": fail(401, "sin_sesion", "abre la URL") });
    await expect(checkSession()).resolves.toBe(false);
    installFakeApi({});
    await expect(checkSession()).resolves.toBe(true);
  });

  it("acepta el 204 sin cuerpo del cierre de sesión", async () => {
    installFakeApi({ "POST /api/logout": { status: 204, body: null } });
    await expect(postVoid("/api/logout", {})).resolves.toBeUndefined();
  });
});
