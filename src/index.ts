import fs from "fs";
import path from "path";
import AdmZip from "adm-zip";
import { parse as parseToml } from "@iarna/toml";
import { load as loadHtml } from "cheerio";
import { fetch as undiciFetch } from "undici";

const fetchFn: typeof fetch = (globalThis as any).fetch || (undiciFetch as any);

type ModEntry = {
  jar: string;
  modId: string;
  displayName?: string;
  version?: string;
  source: "mods.toml" | "fabric.mod.json";
};

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
const DEFAULT_MODS_DIR = path.resolve(process.cwd(), "mods");

async function fetchHtml(url: string): Promise<string | null> {
  try {
    const resp = await fetchFn(url, {
      headers: { "User-Agent": UA, Accept: "text/html" },
    } as any);
    if (!resp.ok) return null;
    return await resp.text();
  } catch {
    return null;
  }
}

function getTomlFromZip(zip: AdmZip): string | null {
  const entry = zip.getEntry("META-INF/mods.toml") || zip.getEntry("META-INF/neoforge.mods.toml");
  if (!entry) return null;
  try {
    const buf = entry.getData();
    return buf.toString("utf-8");
  } catch {
    return null;
  }
}

function extractModsFromToml(tomlText: string | null, jarName: string): ModEntry[] {
  if (!tomlText) return [];
  let data: any;
  try {
    data = parseToml(tomlText);
  } catch {
    return [];
  }
  const mods: any[] = Array.isArray(data?.mods) ? data.mods : [];
  return mods
    .map((m) => ({
      jar: jarName,
      modId: String(m?.modId || m?.modid || "").trim(),
      displayName: m?.displayName ? String(m.displayName) : undefined,
      version: m?.version ? String(m.version) : undefined,
      source: "mods.toml" as const,
    }))
    .filter((m) => !!m.modId);
}

function extractFabricMod(zip: AdmZip, jarName: string): ModEntry[] {
  const entry = zip.getEntry("fabric.mod.json");
  if (!entry) return [];
  try {
    const jsonText = entry.getData().toString("utf-8");
    const obj = JSON.parse(jsonText);
    const modId = String(obj?.id || obj?.custom?.modId || "").trim();
    if (!modId) return [];
    return [
      {
        jar: jarName,
        modId,
        displayName: obj?.name ? String(obj.name) : undefined,
        version: obj?.version ? String(obj.version) : undefined,
        source: "fabric.mod.json",
      },
    ];
  } catch {
    return [];
  }
}

function listModEntriesFromJar(jarPath: string): ModEntry[] {
  try {
    const zip = new AdmZip(jarPath);
    const jarName = path.basename(jarPath);
    const mods = extractModsFromToml(getTomlFromZip(zip), jarName);
    if (mods.length) return mods;
    return extractFabricMod(zip, jarName);
  } catch {
    return [];
  }
}

function normalizeText(s: string | undefined | null): string {
  return String(s || "").toLowerCase().replace(/\s+/g, "");
}

function absoluteHref(href: string): string {
  const trimmed = href.trim().replace(/^[`'"]|[`'"]$/g, "");
  if (trimmed.startsWith("//")) return "https:" + trimmed;
  if (trimmed.startsWith("http")) return trimmed;
  return "https://www.mcmod.cn" + (trimmed.startsWith("/") ? "" : "/") + trimmed;
}

async function searchClassLink(key: string, preferTexts: string[]): Promise<string | null> {
  const url = `https://search.mcmod.cn/s?key=${encodeURIComponent(key)}`;
  const html = await fetchHtml(url);
  if (!html) return null;
  const $ = loadHtml(html);
  const candidates: { href: string; text: string }[] = [];
  $(".search-result-list .result-item").each((_, el) => {
    const headLinks = $(el).find(".head a[href]");
    headLinks.each((__, a) => {
      const href = $(a).attr("href") || "";
      const text = $(a).text().trim();
      if (href.includes("/class/")) {
        candidates.push({ href: absoluteHref(href), text });
      }
    });
  });
  if (!candidates.length) return null;

  const prefers = preferTexts.map(normalizeText).filter(Boolean);
  const found = candidates.find((c) => {
    const nt = normalizeText(c.text);
    return prefers.some((p) => nt.includes(p));
  });
  return (found || candidates[0]).href;
}

async function parseClassPage(url: string): Promise<{ envText: string | null; loaders: string[] }>{
  const html = await fetchHtml(url);
  if (!html) return { envText: null, loaders: [] };
  const $ = loadHtml(html);
  let envText: string | null = null;
  let loaders: string[] = [];
  $(".class-info .class-info-left ul li").each((_, li) => {
    const txt = $(li).text().replace(/\s+/g, " ").trim();
    if (txt.includes("运行环境")) {
      envText = txt.split("运行环境:").pop()?.trim() || null;
    }
    if (txt.includes("运作方式")) {
      loaders = $(li)
        .find("a")
        .map((__, a) => $(a).text().trim())
        .get();
    }
  });
  return { envText, loaders };
}

function classifyEnvironment(envText: string | null): "client" | "server" | "both" | "unknown" {
  if (!envText) return "unknown";
  const t = envText.replace(/\s+/g, "");
  if ((t.includes("客户端需装") && t.includes("服务端需装")) || (t.includes("通用") && t.includes("需装"))) return "both";
  if (t.includes("客户端需装")) return "client";
  if (t.includes("服务端需装")) return "server";
  if (t.includes("客户端可选") && t.includes("服务端可选")) return "both";
  if (t.includes("仅客户端")) return "client";
  if (t.includes("仅服务端") || t.includes("仅服务器端")) return "server";
  return t.includes("客户端") && t.includes("服务端") ? "both" : "unknown";
}

async function analyzeJar(jarPath: string) {
  const entries = listModEntriesFromJar(jarPath);
  if (!entries.length) {
    return [
      {
        jar: path.basename(jarPath),
        modId: null,
        displayName: null,
        version: null,
        envText: null,
        classification: "unknown",
        loaders: [],
        classUrl: null,
        searchUrl: null,
        note: "未识别到mods.toml或fabric.mod.json",
      },
    ];
  }
  const out: any[] = [];
  for (const e of entries) {
    const preferTexts = [e.modId, e.displayName || ""]; 
    const classUrl = (await searchClassLink(e.modId, preferTexts)) || null;
    let envText: string | null = null;
    let loaders: string[] = [];
    if (classUrl) {
      const parsed = await parseClassPage(classUrl);
      envText = parsed.envText;
      loaders = parsed.loaders;
    }
    out.push({
      jar: e.jar,
      modId: e.modId,
      displayName: e.displayName || null,
      version: e.version || null,
      envText,
      classification: classifyEnvironment(envText),
      loaders,
      classUrl,
      searchUrl: `https://search.mcmod.cn/s?key=${encodeURIComponent(e.modId)}`,
      source: e.source,
    });
  }
  return out;
}

async function main() {
  const modsDirArg = process.argv.slice(2)[0];
  const modsDir = path.resolve(process.cwd(), modsDirArg || DEFAULT_MODS_DIR);
  const jars = fs.existsSync(modsDir)
    ? fs
        .readdirSync(modsDir)
        .filter((f) => f.toLowerCase().endsWith(".jar"))
        .map((f) => path.join(modsDir, f))
    : [];
  const results: any[] = [];
  for (const jar of jars) {
    const r = await analyzeJar(jar);
    results.push(...r);
  }
  console.log(JSON.stringify(results, null, 2));
  // 摘要输出
  console.log("\n== 摘要 ==");
  for (const r of results) {
    const loaders = (r.loaders || []).join(",");
    console.log(
      `${r.jar} | ${r.modId} | ${r.displayName} | ${loaders} | ${r.classification} | ${(r.envText || "").trim()}`
    );
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});