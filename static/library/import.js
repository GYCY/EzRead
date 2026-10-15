async function importFiles(files) {
  const pdfs = [...files].filter(file => file.name.toLowerCase().endsWith(".pdf"));
  if (!pdfs.length) { toast("请选择 PDF 文件", "warn"); return; }
  if (state.importing) { toast("正在导入，请等待当前批次完成", "warn"); return; }
  state.importing = true; $("#import-progress-title").textContent = `正在导入 ${pdfs.length} 篇论文`; $("#import-progress").classList.remove("hidden");
  try {
    const form = new FormData(); pdfs.forEach(file => form.append("files", file));
    const data = await api("/api/import", { method: "POST", body: form });
    await loadLibrary(); setView("all");
    const papers = data.papers || [];
    const added = papers.filter(p => !p.duplicate);
    const duplicates = papers.length - added.length;
    if (duplicates) toast(`${added.length ? `已导入：${added.length} 篇论文；` : ""}重复导入：${duplicates} 篇论文（文献库中已存在）`, "warn", 9000);
    else if (added.length) toast(`已导入：${added.length} 篇论文`);
    else if (!(data.errors || []).length) toast("未导入论文，请检查所选文件", "warn");
    for (const error of data.errors || []) toast(asText(error), "error", 9000);
    $("#import-progress").classList.add("hidden");
    for (const p of added) {
      if (p.metadata_enrichment?.missing?.length && p.metadata_enrichment.status === "needs_consent") await openPublicationLookup(p);
    }
  } finally { state.importing = false; $("#import-progress").classList.add("hidden"); $("#file-input").value = ""; }
}
