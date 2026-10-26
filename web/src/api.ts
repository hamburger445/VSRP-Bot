export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const csrf = document.cookie
    .split("; ")
    .find((c) => c.startsWith("vsrp_cad_csrf="))
    ?.split("=")[1];
  if (csrf && init.method && init.method !== "GET") {
    headers["X-CSRF"] = csrf;
  }
  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (res.status === 401) {
    throw Object.assign(new Error("unauthenticated"), { status: 401 });
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw Object.assign(new Error(typeof detail === "string" ? detail : "Request failed"), {
      status: res.status,
    });
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type Me = {
  discord_user_id: number;
  username: string;
  global_name?: string;
  permissions: string[];
  needs_dm_verify: boolean;
  dm_verified_at?: string | null;
};
