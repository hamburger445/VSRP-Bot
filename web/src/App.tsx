import { useEffect, useMemo, useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  Bell,
  Car,
  ClipboardList,
  FileText,
  FileWarning,
  Flame,
  LayoutDashboard,
  Map as MapIcon,
  Menu,
  PanelLeft,
  Radio,
  Search,
  Shield,
  Siren,
  Truck,
  UserRound,
  Users,
} from "lucide-react";
import { Me, api } from "./api";
import { CitationForm, ServeWarrantForm, WarrantIssueForm } from "./forms";
import { DataTable, EmptyState, PageHeader, Panel, StatCard, StatusBadge, Toolbar } from "./ui";

function has(me: Me | null, ...keys: string[]) {
  if (!me) return false;
  if (me.permissions.includes("cad.admin")) return true;
  return keys.some((k) => me.permissions.includes(k));
}

function takeSessionFromHash() {
  const hash = window.location.hash || "";
  const m = /(?:^[#&]|#)cad=([^&]+)/.exec(hash);
  if (!m) return false;
  try {
    sessionStorage.setItem("vsrp_cad", decodeURIComponent(m[1]));
    localStorage.removeItem("vsrp_cad");
  } catch {
    /* ignore */
  }
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return true;
}

function startDiscordLogin() {
  try {
    sessionStorage.removeItem("vsrp_cad");
    localStorage.removeItem("vsrp_cad");
  } catch {
    /* ignore */
  }
  document.cookie = "vsrp_cad=; Path=/; Max-Age=0";
  window.location.href = "/api/auth/discord/login";
}

const NAV = [
  {
    label: "Overview",
    items: [
      { to: "/", title: "Dashboard", icon: LayoutDashboard, need: [] as string[] },
      { to: "/dispatch", title: "Live Dispatch", icon: Radio, need: ["dispatch.operate"] },
      { to: "/map", title: "Map", icon: MapIcon, need: ["mdt.police", "mdt.fire", "mdt.ems", "mdt.dot", "dispatch.operate"] },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/mdt", title: "Police MDT", icon: Shield, need: ["mdt.police"] },
      { to: "/fire", title: "Fire", icon: Flame, need: ["mdt.fire"] },
      { to: "/ems", title: "EMS", icon: Activity, need: ["mdt.ems"] },
      { to: "/dot", title: "DOT Operations", icon: Truck, need: ["mdt.dot"] },
      { to: "/civilian", title: "Civilian Portal", icon: UserRound, need: ["civilian.self"] },
    ],
  },
  {
    label: "Records",
    items: [
      { to: "/citations", title: "Uniform Summons", icon: FileText, need: ["citations.manage"] },
      { to: "/warrants", title: "Warrants", icon: FileWarning, need: ["warrants.manage", "records.read"] },
      { to: "/warrants/new", title: "Issue Warrant", icon: FileWarning, need: ["warrants.manage"] },
      { to: "/warrants/serve", title: "Served Warrant", icon: FileWarning, need: ["warrants.manage"] },
      { to: "/bolos", title: "BOLOs", icon: Siren, need: ["bolos.manage", "records.read"] },
      { to: "/shifts", title: "Shifts", icon: Users, need: ["shifts.read"] },
      { to: "/applications", title: "Applications", icon: ClipboardList, need: ["applications.review"] },
    ],
  },
  {
    label: "Administration",
    items: [{ to: "/admin", title: "Audit & Settings", icon: Car, need: ["cad.admin"] }],
  },
];

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  useEffect(() => {
    takeSessionFromHash();
    api<Me>("/api/auth/me")
      .then((user) => {
        if (!user?.discord_user_id) setMe(null);
        else setMe(user);
      })
      .catch(() => setMe(null));
  }, []);
  if (me === undefined) return <div className="main">Loading CAD…</div>;
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/verify" element={me ? <Verify me={me} /> : <Navigate to="/login" />} />
      <Route path="/*" element={me ? <Shell me={me} /> : <Navigate to="/login" />} />
    </Routes>
  );
}

function Login() {
  return (
    <div className="login">
      <div className="login-card">
        <p className="kicker">VSRP Operations</p>
        <h2>Computer-Aided Dispatch</h2>
        <p className="muted">
          Sign in with the Discord account on the main roleplay server. CAD will not open until Discord authorizes you.
        </p>
        <button className="primary" type="button" onClick={startDiscordLogin}>
          Continue with Discord
        </button>
      </div>
    </div>
  );
}

function Verify({ me }: { me: Me }) {
  const nav = useNavigate();
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="login">
      <div className="login-card">
        <p className="kicker">Account security</p>
        <h2>Discord DM verification</h2>
        <p className="muted">
          Elevated CAD consoles require a one-time code sent by the bot. This is not a replacement for Discord login.
        </p>
        <Toolbar>
          <button
            type="button"
            onClick={async () => {
              try {
                await api("/api/auth/dm-code/request", { method: "POST" });
                setMsg("Code queued. Check DMs from the bot.");
              } catch (e: any) {
                setMsg(e.message);
              }
            }}
          >
            Send code
          </button>
        </Toolbar>
        <Toolbar>
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code" />
          <button
            className="primary"
            type="button"
            onClick={async () => {
              try {
                await api("/api/auth/dm-code/verify", { method: "POST", body: JSON.stringify({ code }) });
                nav("/");
                window.location.reload();
              } catch (e: any) {
                setMsg(e.message);
              }
            }}
          >
            Verify
          </button>
        </Toolbar>
        {msg && <p className="muted">{msg}</p>}
        <p className="muted">Signed in as {me.global_name || me.username}</p>
      </div>
    </div>
  );
}

function Gate({ me, need, children }: { me: Me; need: string[]; children: ReactNode }) {
  if (!need.length || has(me, ...need)) return <>{children}</>;
  return (
    <div className="stack">
      <PageHeader title="Access denied" />
      <p className="err">Your Discord roles on the main server do not grant this CAD department or feature.</p>
    </div>
  );
}

function Shell({ me }: { me: Me }) {
  const loc = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [live, setLive] = useState(true);
  const depts = me.departments || [];
  const [dept, setDept] = useState(() => {
    try {
      return sessionStorage.getItem("cad_dept") || depts[0]?.key || "";
    } catch {
      return depts[0]?.key || "";
    }
  });
  useEffect(() => {
    api("/api/health")
      .then((h: any) => setLive(Boolean(h.database)))
      .catch(() => setLive(false));
  }, []);
  const groups = useMemo(
    () =>
      NAV.map((g) => ({
        ...g,
        items: g.items.filter((item) => (!item.need.length || has(me, ...item.need)) && item.title.toLowerCase().includes(q.toLowerCase())),
      })).filter((g) => g.items.length),
    [me, q],
  );
  const title = groups.flatMap((g) => g.items).find((i) => i.to === loc.pathname)?.title || "CAD";
  const initials = (me.global_name || me.username || "CAD").slice(0, 2).toUpperCase();
  return (
    <div className={`app-shell${collapsed ? " collapsed" : ""}`}>
      <aside className={`sidebar${open ? " open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">VS</div>
          <div className="brand-copy">
            <strong>VSRP CAD</strong>
            <span className="muted tiny">Command center</span>
          </div>
        </div>
        <nav className="nav-groups">
          {groups.map((g) => (
            <div key={g.label}>
              <div className="nav-label">{g.label}</div>
              {g.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/" || item.to === "/warrants"}
                    className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
                    onClick={() => setOpen(false)}
                    title={item.title}
                  >
                    <Icon size={16} />
                    <span>{item.title}</span>
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <button className="icon-btn mobile-toggle" type="button" onClick={() => setOpen((v) => !v)} aria-label="Open navigation">
            <Menu size={16} />
          </button>
          <button className="icon-btn" type="button" onClick={() => setCollapsed((v) => !v)} aria-label="Collapse sidebar">
            <PanelLeft size={16} />
          </button>
          <div>
            <div className="crumb">Operations / {title}</div>
          </div>
          <div className="search">
            <label className="muted tiny" htmlFor="nav-filter" style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <Search size={14} />
              <input id="nav-filter" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter modules…  /" />
            </label>
          </div>
          <span className={`live-dot${live ? "" : " off"}`}>
            <i />
            {live ? "Systems online" : "Degraded"}
          </span>
          {depts.length > 0 && (
            <label className="dept-select">
              <span className="muted tiny">Department</span>
              <select
                value={dept}
                onChange={(e) => {
                  setDept(e.target.value);
                  try {
                    sessionStorage.setItem("cad_dept", e.target.value);
                  } catch {
                    /* ignore */
                  }
                }}
              >
                {depts.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Bell size={16} className="muted" />
          <div className="userchip">
            <div className="avatar">{initials}</div>
            <span>{me.global_name || me.username}</span>
            <button
              type="button"
              onClick={async () => {
                await api("/api/auth/logout", { method: "POST" });
                try {
                  localStorage.removeItem("vsrp_cad");
                  sessionStorage.removeItem("vsrp_cad");
                } catch {
                  /* ignore */
                }
                window.location.href = "/login";
              }}
            >
              Sign out
            </button>
          </div>
        </header>
        <div className="main">
          {me.needs_dm_verify && (
            <div className="banner">
              DM verification required for elevated consoles. <NavLink to="/verify">Verify now</NavLink>
            </div>
          )}
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/civilian" element={<Gate me={me} need={["civilian.self"]}><Civilian /></Gate>} />
            <Route path="/mdt" element={<Gate me={me} need={["mdt.police"]}><Mdt /></Gate>} />
            <Route path="/mdt/people/:id" element={<Gate me={me} need={["mdt.police", "records.read"]}><Person /></Gate>} />
            <Route path="/dispatch" element={<Gate me={me} need={["dispatch.operate"]}><Dispatch /></Gate>} />
            <Route path="/fire" element={<Gate me={me} need={["mdt.fire"]}><DeptCalls path="/api/fire/calls" title="Fire" /></Gate>} />
            <Route path="/ems" element={<Gate me={me} need={["mdt.ems"]}><Ems /></Gate>} />
            <Route path="/dot" element={<Gate me={me} need={["mdt.dot"]}><Dot /></Gate>} />
            <Route path="/map" element={<MapPage />} />
            <Route path="/citations" element={<Gate me={me} need={["citations.manage"]}><CitationForm /></Gate>} />
            <Route path="/warrants" element={<Gate me={me} need={["warrants.manage", "records.read"]}><Warrants /></Gate>} />
            <Route path="/warrants/new" element={<Gate me={me} need={["warrants.manage"]}><WarrantIssueForm /></Gate>} />
            <Route path="/warrants/serve" element={<Gate me={me} need={["warrants.manage"]}><ServeWarrantForm /></Gate>} />
            <Route path="/bolos" element={<Gate me={me} need={["bolos.manage", "records.read"]}><Bolos /></Gate>} />
            <Route path="/shifts" element={<Gate me={me} need={["shifts.read"]}><Shifts /></Gate>} />
            <Route path="/applications" element={<Gate me={me} need={["applications.review"]}><Applications /></Gate>} />
            <Route path="/admin" element={<Gate me={me} need={["cad.admin"]}><Admin /></Gate>} />
          </Routes>
        </div>
      </div>
    </div>
  );
}

function Dashboard() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/api/dashboard").then(setData).catch((e) => setErr(e.message));
  }, []);
  if (err) return <p className="err">{err}</p>;
  if (!data) return <p className="muted">Loading dashboard…</p>;
  const empty = !data.open_calls && !data.pending_calls && !(data.recent_calls || []).length;
  return (
    <div className="stack">
      <PageHeader kicker="Overview" title="Operations dashboard" />
      {empty && (
        <EmptyState
          title="No live incidents yet"
          body="Create a call from Live Dispatch, add a unit, and this board will populate from the database."
        />
      )}
      <div className="stats">
        <StatCard label="Open calls" value={data.open_calls ?? 0} />
        <StatCard label="Pending dispatch" value={data.pending_calls ?? 0} />
        <StatCard label="Active BOLOs" value={data.active_bolos ?? 0} />
        <StatCard label="Your unpaid citations" value={data.my_unpaid_citations ?? 0} />
      </div>
      <Panel title="Recent calls">
        <DataTable
          searchable
          rows={data.recent_calls}
          cols={[
            ["call_number", "Call"],
            ["discipline", "Disc."],
            ["priority", "Pri"],
            ["status", "Status"],
            ["nature", "Nature"],
            ["location_text", "Location"],
          ]}
          empty="No calls in the database."
        />
      </Panel>
    </div>
  );
}

function Civilian() {
  const [data, setData] = useState<any>(null);
  const [name, setName] = useState("");
  useEffect(() => {
    api("/api/civilian/me").then(setData).catch(() => setData({ error: true }));
  }, []);
  if (!data) return <p className="muted">Loading…</p>;
  if (data.error) return <p className="err">Could not load civilian file.</p>;
  const uid = data.user?.user_id;
  return (
    <div className="stack">
      <PageHeader kicker="Civilian" title="Civilian portal" />
      <p className="muted">Discord ID {uid}. Fictional character names are not proof of identity.</p>
      <Panel title="Characters">
        <DataTable rows={data.characters} cols={[["display_name", "Name"], ["dob", "DOB"]]} empty="No characters yet." />
        <Toolbar>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Character name" />
          <button
            className="primary"
            type="button"
            onClick={async () => {
              await api(`/api/people/${uid}/characters`, {
                method: "POST",
                body: JSON.stringify({ display_name: name, is_primary: true }),
              });
              setData(await api("/api/civilian/me"));
            }}
          >
            Add character
          </button>
        </Toolbar>
      </Panel>
      <Panel title="Vehicles">
        <DataTable rows={data.vehicles} cols={[["plate", "Plate"], ["make", "Make"], ["model", "Model"], ["status", "Status"]]} empty="No registered vehicles." />
      </Panel>
      <Panel title="Citations">
        <DataTable rows={data.citations} cols={[["case_number", "Case"], ["violation", "Violation"], ["fine", "Fine"], ["payment_status", "Status"]]} empty="No citations." />
        {(data.citations || []).filter((c: any) => c.payment_status === "unpaid").map((c: any) => (
          <button
            key={c.id}
            type="button"
            onClick={async () => {
              await api(`/api/citations/${c.id}/pay`, { method: "POST" });
              setData(await api("/api/civilian/me"));
            }}
          >
            Pay {c.case_number}
          </button>
        ))}
      </Panel>
      <Panel title="Warrants">
        <DataTable rows={data.warrants} cols={[["case_number", "Case"], ["reason", "Reason"], ["status", "Status"]]} empty="No warrants." />
      </Panel>
    </div>
  );
}

function Mdt() {
  const [q, setQ] = useState("");
  const [plate, setPlate] = useState("");
  const [people, setPeople] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const nav = useNavigate();
  return (
    <div className="stack">
      <PageHeader
        kicker="Police"
        title="Mobile Data Terminal"
        actions={
          <>
            <NavLink to="/citations">Uniform summons</NavLink>
            <NavLink to="/warrants/new">Issue warrant</NavLink>
            <NavLink to="/warrants/serve">Record service</NavLink>
          </>
        }
      />
      <Panel title="Lookups">
        <Toolbar>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or Discord ID" />
          <button
            type="button"
            onClick={async () => {
              const r = await api<{ results: any[] }>(`/api/people?q=${encodeURIComponent(q)}`);
              setPeople(r.results);
            }}
          >
            Search people
          </button>
          <input value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="Plate" />
          <button
            type="button"
            onClick={async () => {
              const r = await api<{ results: any[] }>(`/api/vehicles?plate=${encodeURIComponent(plate)}`);
              setVehicles(r.results);
            }}
          >
            Search plate
          </button>
        </Toolbar>
        {people.map((p) => (
          <button key={p.user_id} type="button" onClick={() => nav(`/mdt/people/${p.user_id}`)}>
            {p.display_name || p.user_id}
          </button>
        ))}
        <DataTable rows={vehicles} cols={[["plate", "Plate"], ["make", "Make"], ["model", "Model"], ["user_id", "Owner"]]} empty={plate ? "No vehicles." : "Search a plate."} />
      </Panel>
    </div>
  );
}

function Person() {
  const { id } = useParams();
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    api(`/api/people/${id}`).then(setData).catch(() => setData({ error: true }));
  }, [id]);
  if (!data) return <p className="muted">Loading…</p>;
  if (data.error) return <p className="err">Not found or no access.</p>;
  return (
    <>
      <PageHeader kicker="Records" title={`Person ${id}`} />
      <p className="muted">
        License suspended: {data.user.license_suspended ? "yes" : "no"} · Registration suspended:{" "}
        {data.user.registration_suspended ? "yes" : "no"}
      </p>
      <Panel title="Vehicles">
        <DataTable rows={data.vehicles} cols={[["plate", "Plate"], ["make", "Make"], ["model", "Model"], ["status", "Status"]]} empty="None" />
      </Panel>
      <Panel title="Citations">
        <DataTable rows={data.citations} cols={[["case_number", "Case"], ["violation", "Violation"], ["payment_status", "Pay"]]} empty="None" />
      </Panel>
      <Panel title="Warrants">
        <DataTable rows={data.warrants} cols={[["case_number", "Case"], ["reason", "Reason"], ["status", "Status"]]} empty="None" />
      </Panel>
    </>
  );
}

function Dispatch() {
  const [calls, setCalls] = useState<any[]>([]);
  const [units, setUnits] = useState<any[]>([]);
  const [nature, setNature] = useState("");
  const [loc, setLoc] = useState("");
  const [disc, setDisc] = useState("police");
  const [callsign, setCallsign] = useState("");
  const [dept, setDept] = useState("police2");
  const refresh = async () => {
    const c = await api<{ results: any[] }>("/api/calls");
    const u = await api<{ results: any[] }>("/api/units");
    setCalls(c.results);
    setUnits(u.results);
  };
  useEffect(() => {
    refresh();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/ws`);
    ws.onmessage = () => refresh();
    return () => ws.close();
  }, []);
  const pending = calls.filter((c) => c.status === "pending" || c.status === "new");
  return (
    <div className="stack">
      <PageHeader kicker="Dispatch" title="Live dispatch" />
      <Toolbar>
        <select value={disc} onChange={(e) => setDisc(e.target.value)}>
          <option>police</option>
          <option>fire</option>
          <option>ems</option>
          <option>dot</option>
          <option>multi</option>
        </select>
        <input value={nature} onChange={(e) => setNature(e.target.value)} placeholder="Nature" />
        <input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Location" />
        <button
          className="primary"
          type="button"
          onClick={async () => {
            await api("/api/calls", {
              method: "POST",
              body: JSON.stringify({ discipline: disc, nature, location_text: loc, priority: 3 }),
            });
            refresh();
          }}
        >
          Create call
        </button>
      </Toolbar>
      <div className="grid-2">
        <Panel title={`Call queue (${calls.length})`} actions={<span className="muted tiny">{pending.length} unassigned/pending</span>}>
          {!calls.length && <EmptyState title="No calls" body="Create a call to start the board." />}
          {calls.map((c) => (
            <article key={c.id} className="call-card">
              <header>
                <strong>{c.call_number}</strong>
                <span>
                  <StatusBadge value={c.discipline} /> <StatusBadge value={c.status} />
                </span>
              </header>
              <div className="muted">
                Pri {c.priority} · {c.nature} @ {c.location_text}
              </div>
              <Toolbar>
                {units.map((u) => (
                  <button
                    key={u.id}
                    type="button"
                    onClick={async () => {
                      await api(`/api/calls/${c.id}/assign`, { method: "POST", body: JSON.stringify({ unit_id: u.id }) });
                      refresh();
                    }}
                  >
                    Assign {u.callsign}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={async () => {
                    await api(`/api/calls/${c.id}/close`, { method: "POST" });
                    refresh();
                  }}
                >
                  Close
                </button>
              </Toolbar>
            </article>
          ))}
        </Panel>
        <Panel title="Unit roster">
          <Toolbar>
            <input value={callsign} onChange={(e) => setCallsign(e.target.value)} placeholder="Callsign" />
            <select value={dept} onChange={(e) => setDept(e.target.value)}>
              <option>police1</option>
              <option>police2</option>
              <option>fire1</option>
              <option>fire2</option>
              <option>fire3</option>
              <option>ems</option>
              <option>dot</option>
              <option>dispatch</option>
            </select>
            <button
              type="button"
              onClick={async () => {
                await api("/api/units", { method: "POST", body: JSON.stringify({ callsign, department: dept }) });
                refresh();
              }}
            >
              Add unit
            </button>
          </Toolbar>
          {units.map((u) => (
            <div key={u.id} className="unit-row">
              <strong>{u.callsign}</strong>
              <span className="muted">{u.department}</span>
              <StatusBadge value={u.status} />
              {["available", "en_route", "on_scene", "busy", "out_of_service"].map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={async () => {
                    await api(`/api/units/${u.id}/status`, { method: "POST", body: JSON.stringify({ status: s }) });
                    refresh();
                  }}
                >
                  {s.replace("_", " ")}
                </button>
              ))}
            </div>
          ))}
          <h3 style={{ marginTop: 16 }}>Pager</h3>
          <Pager />
        </Panel>
      </div>
    </div>
  );
}

function Pager() {
  const [department, setDepartment] = useState("fire1");
  const [message, setMessage] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <Toolbar>
      <select value={department} onChange={(e) => setDepartment(e.target.value)}>
        <option value="fire1">WFR</option>
        <option value="fire2">RRVFD</option>
        <option value="fire3">MMVFD</option>
        <option value="police1">WCSO</option>
        <option value="police2">WPD</option>
        <option value="ems">EMS</option>
        <option value="dot">DOT</option>
        <option value="dispatch">Dispatch</option>
      </select>
      <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Alert message" />
      <button
        className="danger"
        type="button"
        onClick={async () => {
          await api("/api/dispatch/page", { method: "POST", body: JSON.stringify({ department, message }) });
          setMsg("Queued for Discord.");
        }}
      >
        Page
      </button>
      {msg && <span className="muted">{msg}</span>}
    </Toolbar>
  );
}

function DeptCalls({ path, title }: { path: string; title: string }) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    api<{ results: any[] }>(path).then((r) => setRows(r.results));
  }, [path]);
  return (
    <>
      <PageHeader kicker="Operations" title={title} />
      <Panel>
        <DataTable searchable rows={rows} cols={[["call_number", "Call"], ["status", "Status"], ["nature", "Nature"], ["location_text", "Location"]]} empty="No incidents." />
      </Panel>
    </>
  );
}

function Ems() {
  const [data, setData] = useState<any>({ results: [], pcrs: [] });
  const [patient, setPatient] = useState("");
  const [cc, setCc] = useState("");
  useEffect(() => {
    api("/api/ems/calls").then(setData);
  }, []);
  return (
    <>
      <PageHeader kicker="EMS" title="Emergency medical" />
      <Panel title="Calls">
        <DataTable rows={data.results} cols={[["call_number", "Call"], ["status", "Status"], ["nature", "Nature"]]} empty="No EMS calls." />
      </Panel>
      <Panel title="Patient care report (fictional)">
        <Toolbar>
          <input value={patient} onChange={(e) => setPatient(e.target.value)} placeholder="Patient name" />
          <input value={cc} onChange={(e) => setCc(e.target.value)} placeholder="Chief complaint" />
          <button
            type="button"
            onClick={async () => {
              await api("/api/ems/pcr", { method: "POST", body: JSON.stringify({ patient_name: patient, chief_complaint: cc }) });
              setData(await api("/api/ems/calls"));
            }}
          >
            File PCR
          </button>
        </Toolbar>
        <DataTable rows={data.pcrs} cols={[["patient_name", "Patient"], ["chief_complaint", "Complaint"], ["disposition", "Disp."]]} empty="No PCRs." />
      </Panel>
    </>
  );
}

function Dot() {
  const [data, setData] = useState<any>({ results: [], closures: [] });
  const [loc, setLoc] = useState("");
  useEffect(() => {
    api("/api/dot/calls").then(setData);
  }, []);
  return (
    <>
      <PageHeader kicker="DOT" title="Transportation operations" />
      <Panel title="Calls">
        <DataTable rows={data.results} cols={[["call_number", "Call"], ["status", "Status"], ["nature", "Nature"]]} empty="No DOT calls." />
      </Panel>
      <Panel title="Road closures">
        <Toolbar>
          <input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Location" />
          <button
            className="primary"
            type="button"
            onClick={async () => {
              await api("/api/dot/closures", { method: "POST", body: JSON.stringify({ location_text: loc }) });
              setData(await api("/api/dot/calls"));
            }}
          >
            Open closure
          </button>
        </Toolbar>
        {(data.closures || []).map((c: any) => (
          <div key={c.id} className="unit-row">
            <span>
              {c.location_text} — <StatusBadge value={c.status} />
            </span>
            {c.status === "open" && (
              <button
                type="button"
                onClick={async () => {
                  await api(`/api/dot/closures/${c.id}/clear`, { method: "POST" });
                  setData(await api("/api/dot/calls"));
                }}
              >
                Clear
              </button>
            )}
          </div>
        ))}
      </Panel>
    </>
  );
}

function MapPage() {
  const [data, setData] = useState<any>({ units: [], unplaced: [], calls: [] });
  useEffect(() => {
    api("/api/map/units").then((d) => {
      setData(d);
      const L = window.L;
      if (!L) return;
      const el = document.getElementById("map");
      if (!el || (el as any)._leaflet) return;
      const map = L.map("map").setView([36.95, -81.08], 11);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
      }).addTo(map);
      (d as any).units.forEach((u: any) => {
        L.marker([u.lat, u.lng]).addTo(map).bindPopup(`${u.callsign} (${u.status})`);
      });
      (d as any).calls.forEach((c: any) => {
        L.circleMarker([c.lat, c.lng], { color: "#ef4444" }).addTo(map).bindPopup(c.call_number);
      });
    });
  }, []);
  return (
    <>
      <PageHeader kicker="Map" title="Tactical map" />
      <p className="muted">Positions are manual. Discord does not provide GPS. Unplaced units stay off the map.</p>
      <div id="map" />
      <Panel title="Unplaced units">
        <DataTable rows={data.unplaced} cols={[["callsign", "Unit"], ["department", "Dept"], ["status", "Status"]]} empty="All units placed or none exist." />
      </Panel>
    </>
  );
}

function Warrants() {
  const [rows, setRows] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const load = () => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (status) params.set("status", status);
    const qs = params.toString();
    return api<{ results: any[] }>(`/api/warrants${qs ? `?${qs}` : ""}`).then((r) => setRows(r.results));
  };
  useEffect(() => {
    load();
  }, []);
  return (
    <div className="stack">
      <PageHeader
        kicker="Records"
        title="Warrant file"
        actions={
          <>
            <NavLink to="/warrants/new">Issue warrant</NavLink>
            <NavLink to="/warrants/serve">Record service</NavLink>
          </>
        }
      />
      <Panel title="Search">
        <Toolbar>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Case, accused, type, jurisdiction" />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="served">Served</option>
            <option value="cleared">Cleared</option>
          </select>
          <button type="button" onClick={() => load()}>
            Filter
          </button>
        </Toolbar>
      </Panel>
      <Panel title="Results">
        <DataTable
          searchable
          rows={rows}
          cols={[
            ["case_number", "Case"],
            ["reason", "Accused / type"],
            ["status", "Status"],
            ["issued_by", "Issued by"],
          ]}
          empty="No warrants match those filters."
        />
        {rows
          .filter((w) => w.active === 1 || w.active === true)
          .map((w) => (
            <div key={w.id} className="unit-row">
              <span>
                #{w.case_number} — <StatusBadge value={w.status} />
              </span>
              <NavLink to="/warrants/serve">Serve form</NavLink>
              <button
                type="button"
                onClick={async () => {
                  await api(`/api/warrants/${w.id}/clear`, { method: "POST" });
                  load();
                }}
              >
                Clear
              </button>
            </div>
          ))}
      </Panel>
    </div>
  );
}

function Bolos() {
  const [rows, setRows] = useState<any[]>([]);
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState("person");
  const load = () => api<{ results: any[] }>("/api/bolos").then((r) => setRows(r.results));
  useEffect(() => {
    load();
  }, []);
  return (
    <>
      <PageHeader kicker="Records" title="BOLOs" />
      <Panel>
        <Toolbar>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="person">person</option>
            <option value="vehicle">vehicle</option>
          </select>
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description" />
          <button
            className="primary"
            type="button"
            onClick={async () => {
              await api("/api/bolos", { method: "POST", body: JSON.stringify({ kind, description: desc }) });
              load();
            }}
          >
            Create
          </button>
        </Toolbar>
        {rows.map((b) => (
          <div key={b.id} className="unit-row">
            <span>
              {b.kind} <StatusBadge value={b.status} /> {b.description}
            </span>
            {b.status === "active" && (
              <button
                type="button"
                onClick={async () => {
                  await api(`/api/bolos/${b.id}/clear`, { method: "POST" });
                  load();
                }}
              >
                Clear
              </button>
            )}
          </div>
        ))}
      </Panel>
    </>
  );
}

function Shifts() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    api<{ results: any[] }>("/api/shifts/active").then((r) => setRows(r.results));
  }, []);
  return (
    <>
      <PageHeader kicker="Personnel" title="Active shifts" />
      <p className="muted">Start/stop remains on FD/PD Discord (`/shift`).</p>
      <Panel>
        <DataTable rows={rows} cols={[["user_id", "User"], ["guild_id", "Guild"], ["status", "Status"], ["started_at", "Started"]]} empty="No active shifts." />
      </Panel>
    </>
  );
}

function Applications() {
  const [rows, setRows] = useState<any[]>([]);
  const [detail, setDetail] = useState<any>(null);
  useEffect(() => {
    api<{ results: any[] }>("/api/applications").then((r) => setRows(r.results));
  }, []);
  return (
    <>
      <PageHeader kicker="Management" title="Applications" />
      <p className="muted">Approvals stay in Discord so role grants are not duplicated.</p>
      <Toolbar>
        {rows.map((a) => (
          <button key={a.id} type="button" onClick={async () => setDetail(await api(`/api/applications/${a.id}`))}>
            #{a.id} {a.department} {a.status}
          </button>
        ))}
      </Toolbar>
      {detail && (
        <Panel title="Answers">
          {(detail.answers || []).map((x: any, i: number) => (
            <p key={i}>
              <strong>{x.question}</strong>
              <br />
              {x.answer}
            </p>
          ))}
        </Panel>
      )}
    </>
  );
}

function Admin() {
  const [audit, setAudit] = useState<any>({ cad: [], bot: [] });
  useEffect(() => {
    api("/api/admin/audit").then(setAudit);
  }, []);
  return (
    <>
      <PageHeader kicker="Administration" title="Audit log" />
      <Panel title="CAD audit">
        <DataTable searchable rows={audit.cad} cols={[["created_at", "When"], ["actor_id", "Actor"], ["action", "Action"], ["entity_type", "Entity"]]} empty="No CAD audit events." />
      </Panel>
    </>
  );
}
