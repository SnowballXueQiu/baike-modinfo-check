use clap::Parser;
use reqwest::blocking::Client;
use reqwest::header::USER_AGENT;
use scraper::{Html, Selector};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use zip::ZipArchive;

#[derive(Parser, Debug)]
#[command(version, about = "MC百科运行环境分类器 (Rust)")]
struct Args {
    /// 模组目录（默认：项目下 mods）
    mods_dir: Option<PathBuf>,

    /// 仅输出 JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct OutputItem {
    jar: String,
    mod_id: Option<String>,
    display_name: Option<String>,
    version: Option<String>,
    env_text: Option<String>,
    classification: String,
    loaders: Vec<String>,
    class_url: Option<String>,
    search_url: Option<String>,
    source: Option<String>,
    note: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ModsToml {
    mods: Option<Vec<ModEntryToml>>,
}

#[derive(Debug, Deserialize)]
struct ModEntryToml {
    #[serde(rename = "modId")]
    mod_id: Option<String>,
    #[serde(rename = "modid")]
    modid_lower: Option<String>,
    #[serde(rename = "displayName")]
    display_name: Option<String>,
    version: Option<String>,
}

#[derive(Debug, Deserialize)]
struct FabricModJson {
    id: Option<String>,
    name: Option<String>,
    version: Option<String>,
    custom: Option<serde_json::Value>,
}

const UA: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

fn read_zip_entry_string<R: Read + std::io::Seek>(zip: &mut ZipArchive<R>, path: &str) -> Option<String> {
    match zip.by_name(path) {
        Ok(mut f) => {
            let mut buf = String::new();
            if f.read_to_string(&mut buf).is_ok() { Some(buf) } else { None }
        }
        Err(_) => None,
    }
}

fn list_mod_entries_from_jar(jar_path: &Path) -> Vec<OutputItem> {
    let mut entries: Vec<OutputItem> = Vec::new();
    let jar_name = jar_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
    let f = match File::open(jar_path) { Ok(f) => f, Err(_) => return entries };
    let mut zip = match ZipArchive::new(f) { Ok(z) => z, Err(_) => return entries };

    // mods.toml / neoforge.mods.toml
    if let Some(toml_text) = read_zip_entry_string(&mut zip, "META-INF/mods.toml")
        .or_else(|| read_zip_entry_string(&mut zip, "META-INF/neoforge.mods.toml"))
    {
        if let Ok(toml_val) = toml::from_str::<ModsToml>(&toml_text) {
            if let Some(mods) = toml_val.mods {
                for m in mods {
                    let mod_id = m.mod_id.or(m.modid_lower);
                    if mod_id.is_some() {
                        entries.push(OutputItem {
                            jar: jar_name.clone(),
                            mod_id: mod_id,
                            display_name: m.display_name,
                            version: m.version,
                            env_text: None,
                            classification: "unknown".into(),
                            loaders: vec![],
                            class_url: None,
                            search_url: None,
                            source: Some("mods.toml".into()),
                            note: None,
                        });
                    }
                }
            }
        }
    } else if let Some(json_text) = read_zip_entry_string(&mut zip, "fabric.mod.json") {
        if let Ok(fmj) = serde_json::from_str::<FabricModJson>(&json_text) {
            let mod_id = fmj.id.or_else(|| fmj.custom.and_then(|c| c.get("modId").and_then(|v| v.as_str()).map(|s| s.to_string())));
            if mod_id.is_some() {
                entries.push(OutputItem {
                    jar: jar_name.clone(),
                    mod_id: mod_id,
                    display_name: fmj.name,
                    version: fmj.version,
                    env_text: None,
                    classification: "unknown".into(),
                    loaders: vec![],
                    class_url: None,
                    search_url: None,
                    source: Some("fabric.mod.json".into()),
                    note: None,
                });
            }
        }
    }

    entries
}

fn fetch_html(client: &Client, url: &str) -> Option<String> {
    let resp = client.get(url).header(USER_AGENT, UA).send().ok()?;
    if !resp.status().is_success() { return None; }
    resp.text().ok()
}

fn absolute_href(href: &str) -> String {
    let h = href.trim().trim_matches(['`', '\'', '"']);
    if h.starts_with("//") { return format!("https:{}", h); }
    if h.starts_with("http") { return h.to_string(); }
    if h.starts_with('/') { return format!("https://www.mcmod.cn{}", h); }
    format!("https://www.mcmod.cn/{}", h)
}

fn search_class_link(client: &Client, key: &str, prefer_texts: &[String]) -> Option<String> {
    let url = format!("https://search.mcmod.cn/s?key={}", urlencoding::encode(key));
    let html = fetch_html(client, &url)?;
    let doc = Html::parse_document(&html);
    let item_sel = Selector::parse("div.search-result-list div.result-item").unwrap();
    let head_link_sel = Selector::parse("div.head a[href]").unwrap();
    let mut candidates: Vec<(String, String)> = Vec::new();
    for item in doc.select(&item_sel) {
        for a in item.select(&head_link_sel) {
            let href = a.value().attr("href").unwrap_or("");
            let text = a.text().collect::<Vec<_>>().join("").trim().to_string();
            if href.contains("/class/") {
                candidates.push((absolute_href(href), text));
            }
        }
    }
    if candidates.is_empty() { return None; }

    let normalize = |s: &str| s.to_lowercase().replace(' ', "");
    let prefers: Vec<String> = prefer_texts.iter().map(|p| normalize(p)).filter(|s| !s.is_empty()).collect();
    for (href, text) in &candidates {
        let nt = normalize(text);
        if prefers.iter().any(|p| nt.contains(p)) { return Some(href.clone()); }
    }
    Some(candidates[0].0.clone())
}

fn parse_class_page(client: &Client, url: &str) -> (Option<String>, Vec<String>) {
    let html = match fetch_html(client, url) { Some(h) => h, None => return (None, vec![]) };
    let doc = Html::parse_document(&html);
    let info_sel = Selector::parse("div.class-info").unwrap();
    let li_sel = Selector::parse("div.class-info-left ul li").unwrap();
    let mut env_text: Option<String> = None;
    let mut loaders: Vec<String> = Vec::new();
    for info in doc.select(&info_sel) {
        for li in info.select(&li_sel) {
            let txt = li.text().collect::<Vec<_>>().join(" ").replace("  ", " ").trim().to_string();
            if txt.contains("运行环境") {
                env_text = txt.split("运行环境:").last().map(|s| s.trim().to_string());
            }
            if txt.contains("运作方式") {
                let a_sel = Selector::parse("a").unwrap();
                loaders = li.select(&a_sel).map(|a| a.text().collect::<Vec<_>>().join("").trim().to_string()).collect();
            }
        }
    }
    (env_text, loaders)
}

fn classify_environment(env_text: Option<&str>) -> String {
    let t = match env_text { Some(s) => s.replace(' ', ""), None => String::new() };
    if t.is_empty() { return "unknown".into(); }
    if (t.contains("客户端需装") && t.contains("服务端需装")) || (t.contains("通用") && t.contains("需装")) { return "both".into(); }
    if t.contains("客户端需装") { return "client".into(); }
    if t.contains("服务端需装") { return "server".into(); }
    if t.contains("客户端可选") && t.contains("服务端可选") { return "both".into(); }
    if t.contains("仅客户端") { return "client".into(); }
    if t.contains("仅服务端") || t.contains("仅服务器端") { return "server".into(); }
    if t.contains("客户端") && t.contains("服务端") { return "both".into(); }
    "unknown".into()
}

fn analyze_dir(mods_dir: &Path) -> Vec<OutputItem> {
    let client = Client::builder().build().unwrap();
    let mut results: Vec<OutputItem> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(mods_dir) {
        for e in entries.flatten() {
            let path = e.path();
            if path.extension().and_then(|s| s.to_str()).map(|s| s.eq_ignore_ascii_case("jar")).unwrap_or(false) {
                let jars = list_mod_entries_from_jar(&path);
                if jars.is_empty() {
                    results.push(OutputItem {
                        jar: path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string(),
                        mod_id: None,
                        display_name: None,
                        version: None,
                        env_text: None,
                        classification: "unknown".into(),
                        loaders: vec![],
                        class_url: None,
                        search_url: None,
                        source: None,
                        note: Some("未识别到mods.toml或fabric.mod.json".into()),
                    });
                } else {
                    for mut item in jars {
                        let mod_id = item.mod_id.clone().unwrap_or_default();
                        let prefer = vec![mod_id.clone(), item.display_name.clone().unwrap_or_default()];
                        let class_url = search_class_link(&client, &mod_id, &prefer.iter().map(|s| s.to_string()).collect::<Vec<_>>());
                        item.search_url = Some(format!("https://search.mcmod.cn/s?key={}", urlencoding::encode(&mod_id)));
                        if let Some(url) = class_url.clone() {
                            let (env_text, loaders) = parse_class_page(&client, &url);
                            item.env_text = env_text.clone();
                            item.classification = classify_environment(item.env_text.as_deref());
                            item.loaders = loaders;
                            item.class_url = Some(url);
                        } else {
                            item.env_text = None;
                            item.classification = "unknown".into();
                            item.loaders = vec![];
                            item.class_url = None;
                        }
                        results.push(item);
                    }
                }
            }
        }
    }
    results
}

fn main() {
    let args = Args::parse();
    let mods_dir = args.mods_dir.unwrap_or_else(|| PathBuf::from("mods"));
    let results = analyze_dir(&mods_dir);
    println!("{}", serde_json::to_string_pretty(&results).unwrap());
    if !args.json {
        println!("\n== 摘要 ==");
        for r in &results {
            println!(
                "{} | {} | {} | {} | {} | {}",
                r.jar,
                r.mod_id.as_deref().unwrap_or(""),
                r.display_name.as_deref().unwrap_or(""),
                r.loaders.join(","),
                r.classification,
                r.env_text.as_deref().unwrap_or("")
            );
        }
    }
}