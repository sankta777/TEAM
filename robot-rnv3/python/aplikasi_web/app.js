"use strict";
// Kontrol RNV3 di browser. Semua keputusan keselamatan ada di server
// (aplikasi.py + jog.Jog); halaman ini hanya meminta dan menampilkan.

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let S = null;                 // status terakhir dari server
let logTerakhir = 0;
let serverHidup = true;
// Preset yang ditolak tercatat di log tab Kontrol saja -- dari tab Preset
// tampak seperti tombol Jalan tidak merespons. Setelah tombol putar ditekan,
// galat berikutnya juga ditampilkan di panel Putar.
let galatPutarDitunggu = false;
const cache = {};             // potongan tampilan yang hanya digambar ulang kalau berubah

const KUNCI_JOG = {
  w: ["y", 1], s: ["y", -1], a: ["rot", -1], d: ["rot", 1],
  // J = kiri, L = kanan dari operator; di robot nyata itu E+ dan E- (lihat jog.ARAH).
  i: ["z", 1], k: ["z", -1], j: ["e", 1], l: ["e", -1],
};

const UKUR = [
  { kunci: "z_belt", nama: "Z belt", pergi: false },
  { kunci: "z_bak", nama: "Z bak", pergi: false },
  { kunci: "bak_koin", nama: "Bak koin", pergi: true },
  { kunci: "bak_tutup_botol", nama: "Bak tutup botol", pergi: true },
  { kunci: "parkir", nama: "Parkir", pergi: true },
];

// ---------------------------------------------------------------- server
async function kirim(aksi, isi = {}, diam = false) {
  try {
    const r = await fetch("/api/perintah", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-RNV3": "1" },
      body: JSON.stringify({ aksi, ...isi }),
    });
    const j = await r.json();
    if (!j.ok && !diam) tulisLokal(j.pesan, "galat");
    return j;
  } catch (e) {
    if (!diam) tulisLokal("server tidak menjawab. Apakah aplikasi.py masih jalan?", "galat");
    return { ok: false };
  }
}

async function tarik() {
  try {
    const r = await fetch(`/api/status?sejak=${logTerakhir}`, { cache: "no-store" });
    S = await r.json();
    serverHidup = true;
    gambar();
  } catch (e) {
    if (serverHidup) tulisLokal("sambungan ke aplikasi.py terputus", "galat");
    serverHidup = false;
    gambarServerMati();
  } finally {
    setTimeout(tarik, tahan.token ? 120 : 250);
  }
}

// ---------------------------------------------------------------- jog
const tahan = { token: null, kunci: null, tombol: null, mulai: null, detak: null };

function tokenBaru() {
  return (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
}

function bolehGerak() {
  return S && S.tersambung && !S.perlu_homing && S.posisi_dipercaya && !S.sibuk;
}

function mulaiJog(sumbu, arah, kunci, tombol) {
  if (tahan.token) return;
  if (!bolehGerak()) {
    if (S && S.sibuk) tulisLokal(`tunggu dulu, robot sedang ${S.sibuk}`, "peringatan");
    return;
  }
  tahan.token = tokenBaru();
  tahan.kunci = kunci;
  tahan.tombol = tombol || $(`[data-jog="${sumbu}"][data-arah="${arah}"]`);
  if (tahan.tombol) tahan.tombol.classList.add("ditahan");
  const token = tahan.token;
  tahan.mulai = kirim("jog", { sumbu, arah, token }).then((j) => {
    if (!j.ok && tahan.token === token) lepasJog();
  });
  // Detak: tanpa ini server menghentikan jog dalam 0,35 s. Itu pengaman
  // kalau tab ditutup atau browser hang selagi tombol "ditahan".
  tahan.detak = setInterval(() => kirim("jog_detak", { token }, true), 100);
}

async function lepasJog() {
  if (!tahan.token) return;
  const token = tahan.token;
  const mulai = tahan.mulai;
  clearInterval(tahan.detak);
  if (tahan.tombol) tahan.tombol.classList.remove("ditahan");
  Object.assign(tahan, { token: null, kunci: null, tombol: null, mulai: null, detak: null });
  // Tunggu "jog" diterima dulu: kalau "lepas" menyalip, server belum kenal
  // tokennya. (Server juga mengingat token yang sudah dilepas, sebagai lapis kedua.)
  try { await mulai; } catch (e) { /* tetap lepas */ }
  kirim("jog_lepas", { token }, true);
}

function pasangJog() {
  $$("[data-jog]").forEach((b) => {
    b.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      b.setPointerCapture(e.pointerId);
      mulaiJog(b.dataset.jog, Number(b.dataset.arah), null, b);
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) =>
      b.addEventListener(ev, () => { if (tahan.tombol === b) lepasJog(); }));
    b.addEventListener("contextmenu", (e) => e.preventDefault());
  });
  window.addEventListener("blur", lepasJog);
  document.addEventListener("visibilitychange", () => { if (document.hidden) lepasJog(); });
}

function dialogTerbuka() {
  return $("#dialog").open || $("#panduan").open;
}

function pasangKeyboard() {
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const di_isian = e.target.closest("input, textarea, select");
    if (e.key === " " && !di_isian) {
      e.preventDefault();
      berhenti();
      return;
    }
    if (di_isian || dialogTerbuka()) return;
    const k = e.key.toLowerCase();
    if (KUNCI_JOG[k]) {
      e.preventDefault();
      if (!e.repeat) mulaiJog(KUNCI_JOG[k][0], KUNCI_JOG[k][1], k);
      return;
    }
    if (e.repeat) return;
    // Tombol keyboard TIDAK disaring dengan bolehGerak(): status di layar bisa
    // tertinggal 250 ms, dan tombol yang hilang tanpa kabar membingungkan.
    // Server yang memutuskan; kalau lengan masih bergerak, alasannya muncul di log.
    if (!S || !S.tersambung) return;
    if (k === "p") kirim("posisi");
    else if (k === "m") kirim("tandai");
    else if (k === "v") kirim("vakum", { nyala: !S.vakum });
    else if (k === "c" && Array.isArray(S.rekam)) kirim("rekam_catat");
    else if (e.key === "?") bukaPanduan();
  });
  document.addEventListener("keyup", (e) => {
    if (tahan.kunci && e.key.toLowerCase() === tahan.kunci) lepasJog();
  });
}

function berhenti() {
  lepasJog();
  kirim("berhenti");
}

// ---------------------------------------------------------------- dialog
// lain = teks tombol ketiga (opsional). Tanpa `lain` hasilnya boolean seperti
// biasa; dengan `lain` hasilnya "ya" | "lain" | "batal".
function tanya({ judul, isi, cek = [], ya = "Lanjut", bahaya = false, lain = null }) {
  const dlg = $("#dialog");
  $("#dialog-judul").textContent = judul;
  const wadah = $("#dialog-isi");
  wadah.innerHTML = "";
  const p = document.createElement("p");
  p.textContent = isi;
  wadah.append(p);
  const kotak = cek.map((teks) => {
    const label = document.createElement("label");
    label.className = "centang";
    label.style.display = "flex";
    label.style.marginTop = "8px";
    const c = document.createElement("input");
    c.type = "checkbox";
    label.append(c, " ", teks);
    wadah.append(label);
    return c;
  });
  const tombolYa = $("#dialog-ya");
  tombolYa.textContent = ya;
  tombolYa.className = bahaya ? "bahaya" : "utama";
  const tombolLain = $("#dialog-lain");
  tombolLain.hidden = !lain;
  tombolLain.textContent = lain || "";
  const perbarui = () => { tombolYa.disabled = !kotak.every((c) => c.checked); };
  kotak.forEach((c) => c.addEventListener("change", perbarui));
  perbarui();
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((selesai) => {
    dlg.addEventListener("close", () => {
      const v = dlg.returnValue || "batal";
      selesai(lain ? v : v === "ya");
    }, { once: true });
  });
}

function bukaPanduan() {
  lepasJog();
  $("#panduan").showModal();
}

const CEK_HOMING = [
  "Lengan sudah digeser sedikit ke depan pakai tangan",
  "Area sekitar 34 cm dari badan robot dan jalur rel bersih",
  "Tangan siap di saklar power",
];

// ---------------------------------------------------------------- log
// Terbaru di BAWAH, seperti terminal, supaya blok beberapa baris (laporan
// pra-terbang) terbaca urut. Gulir otomatis hanya kalau operator sedang di
// dasar log -- kalau sedang membaca ke atas, jangan direbut.
function tambahLog(baris) {
  const ol = $("#log");
  const diDasar = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 24;
  const li = document.createElement("li");
  li.className = baris.jenis || "info";
  const t = document.createElement("time");
  t.textContent = baris.t || new Date().toTimeString().slice(0, 8);
  li.append(t, baris.teks);
  ol.append(li);
  while (ol.children.length > 250) ol.firstChild.remove();
  if (diDasar) ol.scrollTop = ol.scrollHeight;
  if (baris.jenis === "galat" && galatPutarDitunggu) {
    const p = $("#putar-galat");
    p.textContent = baris.teks;
    p.hidden = false;
  }
}

function kirimPutar(aksi, isi) {
  galatPutarDitunggu = true;
  $("#putar-galat").hidden = true;
  return kirim(aksi, isi);
}

function tulisLokal(teks, jenis = "info") {
  if (teks) tambahLog({ teks, jenis });
}

// ---------------------------------------------------------------- gambar
function angka(v, d = 1) {
  return (typeof v === "number" && isFinite(v)) ? v.toFixed(d) : "-";
}

function setStatus(el, teks, kelas) {
  el.textContent = teks;
  el.className = kelas || "";
}

function gambarServerMati() {
  setStatus($("#sambungan"), "aplikasi.py tidak menjawab", "sambungan");
  $$("button").forEach((b) => {
    if (!b.closest("dialog") && b.id !== "tombol-panduan") b.disabled = true;
  });
}

function gambar() {
  // log
  for (const baris of S.log) {
    tambahLog(baris);
    logTerakhir = Math.max(logTerakhir, baris.id);
  }

  // kepala
  const sb = $("#sambungan");
  if (S.tersambung) setStatus(sb, S.sim ? "Simulator" : `Tersambung ${S.port}`, "sambungan ok");
  else setStatus(sb, "Belum tersambung", "sambungan");
  const sibuk = $("#sibuk");
  sibuk.hidden = !S.sibuk;
  sibuk.textContent = S.sibuk ? `Sedang ${S.sibuk}...` : "";
  $("#tombol-berhenti").disabled = !S.tersambung;

  // posisi
  const p = S.posisi || {};
  $("#pos-x").textContent = angka(p.x);
  $("#pos-y").textContent = angka(p.y);
  $("#pos-z").textContent = angka(p.z);
  $("#pos-e").textContent = angka(p.e);

  if (!S.tersambung) {
    setStatus($("#st-homing"), "Homing: -");
    setStatus($("#st-motor"), "Motor: -");
  } else if (!S.posisi_dipercaya) {
    setStatus($("#st-homing"), "Posisi tidak dipercaya, homing ulang", "bahaya");
    setStatus($("#st-motor"), S.motor_terkunci ? "Motor: terkunci" : "Motor: lepas", S.motor_terkunci ? "ok" : "bahaya");
  } else if (S.perlu_homing) {
    setStatus($("#st-homing"), "Homing: belum", "waspada");
    setStatus($("#st-motor"), "Motor: lepas", "bahaya");
  } else {
    setStatus($("#st-homing"), "Homing: sudah", "ok");
    setStatus($("#st-motor"), S.motor_terkunci ? "Motor: terkunci" : "Motor: lepas", S.motor_terkunci ? "ok" : "bahaya");
  }
  if (!S.tersambung) setStatus($("#st-vakum"), "Vakum: -");
  else setStatus($("#st-vakum"), S.vakum ? "Vakum: nyala" : "Vakum: mati", S.vakum ? "waspada" : "");

  const sisa = $("#sisa");
  if (S.jog && S.jog.sisa && S.tersambung && !S.perlu_homing) {
    const [nama, mm] = S.jog.sisa;
    sisa.textContent = mm < 0.3 ? `MENTOK ke ${nama}` : `${nama}: sisa ${angka(mm, 0)} mm`;
    sisa.className = "sisa" + (mm < 0.3 ? " mentok" : mm < 25 ? " dekat" : "");
  } else {
    sisa.textContent = "";
    sisa.className = "sisa";
  }

  // kartu tab kontrol
  $("#kartu-sambung").hidden = S.tersambung;
  $("#kartu-homing").hidden = !S.tersambung || !S.perlu_homing;
  $("#kartu-kerja").hidden = !S.tersambung || S.perlu_homing;
  $("#tombol-homing").disabled = !!S.sibuk;
  $("#form-sambung button").disabled = !!S.sibuk;

  // aktif / redup
  const bisaGerak = bolehGerak();
  $$("[data-jog]").forEach((b) => { if (!b.classList.contains("ditahan")) b.disabled = !bisaGerak; });
  $$('[data-perlu="homing"]').forEach((b) => { b.disabled = !bisaGerak; });
  $$('[data-perlu="tersambung"]').forEach((b) => { b.disabled = !S.tersambung || !!S.sibuk; });
  $$('#kartu-homing [data-aksi="putus"]').forEach((b) => { b.disabled = !!S.sibuk; });
  const vk = $("#tombol-vakum");
  vk.setAttribute("aria-pressed", String(!!S.vakum));

  gambarSetelanJog();
  if (S.tersambung && !S.perlu_homing) gambarPeta();
  gambarUkur();
  gambarPreset();
}

function gambarSetelanJog() {
  if (!S.jog) return;
  const kunciCache = JSON.stringify([S.jog.i_step, S.jog.i_sapuan, S.jog.i_accel]);
  if (cache.jog === kunciCache) return;
  cache.jog = kunciCache;
  const isi = (id, daftar, aktif, nama) => {
    const w = $(id);
    w.innerHTML = "";
    daftar.forEach((v, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = String(v);
      b.setAttribute("aria-pressed", String(i === aktif));
      b.addEventListener("click", () => kirim("setel_jog", { [nama]: i }));
      w.append(b);
    });
  };
  isi("#pilih-step", S.jog.steps, S.jog.i_step, "i_step");
  isi("#pilih-sapuan", S.jog.sapuan, S.jog.i_sapuan, "i_sapuan");
  isi("#pilih-accel", S.jog.accel, S.jog.i_accel, "i_accel");
}

// ---------------------------------------------------------------- peta
const NS = "http://www.w3.org/2000/svg";
const UKURAN_LABEL = 19;   // satuan SVG = mm; peta 800 mm lebar -> ~12 px di laptop

function el(nama, atr, teks) {
  const e = document.createElementNS(NS, nama);
  for (const [k, v] of Object.entries(atr)) e.setAttribute(k, v);
  if (teks != null) e.textContent = teks;
  return e;
}

// koordinat robot (mm, Y ke depan) -> SVG (Y ke bawah)
const titikPeta = (x, y) => [x, -y];
const kutub = (r, derajat) => {
  const a = derajat * Math.PI / 180;
  return titikPeta(r * Math.sin(a), r * Math.cos(a));
};

function gambarPeta() {
  const svg = $("#peta");
  const p = S.posisi;
  const kunciStatis = JSON.stringify([S.peta && S.peta.z, S.batas, S.setelan, S.titik, S.konstanta]);
  if (cache.peta !== kunciStatis) {
    cache.peta = kunciStatis;
    svg.innerHTML = "";
    const latar = el("g", {});
    for (const r of [100, 200, 300, 400]) {
      latar.append(el("circle", { cx: 0, cy: 0, r, class: "p-cincin" }));
      latar.append(el("text", { x: 4, y: -r - 4, class: "p-label", "font-size": UKURAN_LABEL }, `${r / 10} cm`));
    }
    svg.append(latar);

    // wilayah terjangkau di Z sekarang
    if (S.peta) {
      const { sudut, rmin, rmax } = S.peta;
      let ruas = [];
      const ruasSemua = [];
      sudut.forEach((d, i) => {
        if (rmax[i] == null) { if (ruas.length) ruasSemua.push(ruas); ruas = []; }
        else ruas.push(i);
      });
      if (ruas.length) ruasSemua.push(ruas);
      for (const r of ruasSemua) {
        const luar = r.map((i) => kutub(rmax[i], sudut[i]));
        const dalam = r.slice().reverse().map((i) => kutub(rmin[i], sudut[i]));
        const d = "M" + [...luar, ...dalam].map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join("L") + "Z";
        svg.append(el("path", { d, class: "p-jangkau" }));
      }
    }

    // batas belakang dan zona belt
    const ymin = S.batas.y_min;
    if (ymin != null) {
      svg.append(el("line", { x1: -400, y1: -ymin, x2: 400, y2: -ymin, class: "p-batas" }));
      svg.append(el("text", { x: -396, y: -ymin + 16, class: "p-label", "font-size": UKURAN_LABEL }, `batas belakang y ${ymin}`));
    }
    const k = S.konstanta;
    const [bx, by] = titikPeta(-k.belt_x_lebar, k.belt_y + 25);
    svg.append(el("rect", { x: bx, y: by, width: 2 * k.belt_x_lebar, height: 50, rx: 4, class: "p-belt" }));
    svg.append(el("text", { x: bx + 2 * k.belt_x_lebar + 8, y: by + 31, class: "p-label", "font-size": UKURAN_LABEL }, "zona ambil"));

    // bak, parkir, titik tertanda
    const bak = S.setelan.bak;
    for (const [label, [x, y]] of Object.entries(bak)) {
      const [sx, sy] = titikPeta(x, y);
      svg.append(el("rect", { x: sx - 18, y: sy - 18, width: 36, height: 36, rx: 4, class: "p-bak" }));
      svg.append(el("text", { x: sx + 22, y: sy + 5, class: "p-label", "font-size": UKURAN_LABEL }, `bak ${label.replace("_", " ")}`));
    }
    const [px, py] = titikPeta(S.setelan.parkir[0], S.setelan.parkir[1]);
    svg.append(el("circle", { cx: px, cy: py, r: 9, class: "p-parkir" }));
    svg.append(el("text", { x: px + 13, y: py + 18, class: "p-label", "font-size": UKURAN_LABEL }, "parkir"));
    for (const t of S.titik) {
      const [tx, ty] = titikPeta(t.x, t.y);
      svg.append(el("circle", { cx: tx, cy: ty, r: 3.5, class: "p-titik" }));
    }

    svg.append(el("circle", { cx: 0, cy: 0, r: 34, class: "p-badan" }));
    svg.append(el("g", { id: "p-lengan" }));
  }

  // lengan (berubah tiap jog)
  const g = svg.querySelector("#p-lengan");
  if (!g || !p) return;
  g.innerHTML = "";
  const [ux, uy] = titikPeta(p.x, p.y);
  g.append(el("line", { x1: 0, y1: 0, x2: ux, y2: uy, class: "p-lengan" }));
  g.append(el("circle", { cx: ux, cy: uy, r: 9, class: "p-ujung" }));
}

// ---------------------------------------------------------------- ukur
// Sumbu yang diambil tiap ukuran (sama dengan setelan.KUNCI_UKUR).
const SUMBU_UKUR = {
  z_belt: ["z"], z_bak: ["z"], bak_koin: ["x", "y"], bak_tutup_botol: ["x", "y"], parkir: ["x", "y", "z", "e"],
};
// X/Y dihitung dari badan robot, yang ikut bergeser bersama rel.
// Parkir tidak termasuk: dia menyimpan posisi rel-nya sendiri.
const PERLU_REL_NOL = new Set(["bak_koin", "bak_tutup_botol"]);

const tidurMs = (ms) => new Promise((r) => setTimeout(r, ms));

async function simpanUkur(u) {
  const e = S.posisi ? S.posisi.e : 0;
  if (!PERLU_REL_NOL.has(u.kunci) || Math.abs(e) <= 0.5) {
    kirim("ukur_simpan", { kunci: u.kunci });
    return;
  }
  const pilihan = await tanya({
    judul: "Rel tidak di E=0",
    isi: `Rel sekarang di E=${angka(e)}. Posisi X dan Y dihitung dari badan robot, jadi ${u.nama.toLowerCase()} akan meleset sekitar ${angka(Math.abs(e) / 10)} cm saat robot di E=0.`,
    ya: "Rel ke 0 dulu",
    lain: "Tetap simpan",
  });
  if (pilihan === "lain") {
    kirim("ukur_simpan", { kunci: u.kunci, paksa: true });
  } else if (pilihan === "ya") {
    const j = await kirim("rel_nol");
    if (!j.ok) return;
    // Tunggu rel benar-benar sampai, baru simpan.
    for (let i = 0; i < 300; i++) {
      await tidurMs(100);
      if (S && !S.sibuk && S.posisi && Math.abs(S.posisi.e) <= 0.5) {
        kirim("ukur_simpan", { kunci: u.kunci });
        return;
      }
    }
    tulisLokal("rel belum sampai di E=0, posisi tidak disimpan", "peringatan");
  }
}

function gambarUkur() {
  const s = S.setelan;
  const bisaGerak = bolehGerak();

  const kunciUkur = JSON.stringify([s, bisaGerak]);
  if (cache.ukur !== kunciUkur) {
    cache.ukur = kunciUkur;
    const ul = $("#daftar-ukur");
    ul.innerHTML = "";
    for (const u of UKUR) {
      const li = document.createElement("li");
      const nilai = {
        z_belt: `Z ${angka(s.z_belt)}`,
        z_bak: `Z ${angka(s.z_bak)}`,
        bak_koin: `X ${angka(s.bak.koin[0])}  Y ${angka(s.bak.koin[1])}`,
        bak_tutup_botol: `X ${angka(s.bak.tutup_botol[0])}  Y ${angka(s.bak.tutup_botol[1])}`,
        parkir: `X ${angka(s.parkir[0])}  Y ${angka(s.parkir[1])}  Z ${angka(s.parkir[2])}  E ${angka(s.parkir[3])}`,
      }[u.kunci];
      const waktu = s.diukur[u.kunci];
      li.innerHTML = `
        <span class="nama"></span>
        <span class="sekarang" data-kunci=""></span>
        <span class="nilai"><span class="angka"></span><span class="lencana terukur"></span></span>
        <span class="baris-tombol"></span>`;
      li.querySelector(".nama").textContent = u.nama;
      li.querySelector(".sekarang").dataset.kunci = u.kunci;
      li.querySelector(".angka").textContent = nilai;
      // Hanya angka yang SUDAH diukur yang diberi keterangan; yang belum, kosong.
      if (waktu) li.querySelector(".lencana").textContent = `terukur ${waktu}`;
      const tombol = li.querySelector(".baris-tombol");
      const simpan = document.createElement("button");
      simpan.textContent = "Simpan posisi ini";
      simpan.className = "utama";
      simpan.disabled = !bisaGerak;
      simpan.addEventListener("click", () => simpanUkur(u));
      tombol.append(simpan);
      if (u.pergi) {
        const pergi = document.createElement("button");
        pergi.textContent = "Pergi ke sini";
        pergi.disabled = !bisaGerak;
        pergi.addEventListener("click", async () => {
          const ok = await tanya({
            judul: `Pergi ke ${u.nama.toLowerCase()}?`,
            isi: u.kunci === "parkir"
              ? "Lengan geser mendatar ke atas titik parkir, rel pindah ke posisi parkir, lalu lengan turun ke tinggi parkir."
              : "Lengan naik ke Z aman dulu, lalu pindah ke atas titik ini. Tidak turun ke bak.",
            cek: ["Jalur lengan bersih dari benda dan tangan"],
          });
          if (ok) kirim("ukur_pergi", { kunci: u.kunci });
        });
        tombol.append(pergi);
      }
      ul.append(li);
    }
  }

  // "sekarang" berubah tiap jog, jadi diperbarui di LUAR cache, tiap poll.
  const p = S.posisi;
  $$("#daftar-ukur .sekarang").forEach((el) => {
    if (!p || S.perlu_homing) { el.textContent = ""; return; }
    const sumbu = SUMBU_UKUR[el.dataset.kunci];
    el.textContent = "sekarang " + sumbu.map((k) => `${k.toUpperCase()} ${angka(p[k])}`).join("  ");
  });
  $("#ukur-reset").disabled = !S.tersambung || !!S.sibuk;

  const kunciMasalah = JSON.stringify(S.masalah);
  if (cache.masalah !== kunciMasalah) {
    cache.masalah = kunciMasalah;
    const m = $("#masalah");
    m.hidden = !S.masalah.length;
    m.innerHTML = "";
    if (S.masalah.length) {
      const judul = document.createElement("strong");
      judul.textContent = `${S.masalah.length} titik di konfigurasi tidak terjangkau. jalur.py akan menolak jalan sampai ini dibetulkan:`;
      const ul = document.createElement("ul");
      S.masalah.slice(0, 8).forEach((teks) => {
        const li = document.createElement("li");
        li.textContent = teks;
        ul.append(li);
      });
      if (S.masalah.length > 8) {
        const li = document.createElement("li");
        li.textContent = `dan ${S.masalah.length - 8} lagi`;
        ul.append(li);
      }
      m.append(judul, ul);
    }
  }

  // batas fisik
  const kunciBatas = JSON.stringify([S.batas, S.batas_terukur, S.jog && S.jog.sisa]);
  if (cache.batas !== kunciBatas) {
    cache.batas = kunciBatas;
    const dl = $("#daftar-batas");
    dl.innerHTML = "";
    const nama = { y_min: "Y min", rot_maks: "Putar maks (derajat)", x_min: "X min", x_maks: "X maks",
      y_maks: "Y maks", z_min: "Z min", z_maks: "Z maks", e_min: "Rel min", e_maks: "Rel maks" };
    for (const [k, v] of Object.entries(S.batas)) {
      if (!(k in nama)) continue;
      const d = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = nama[k];
      dd.textContent = v == null ? "tidak dibatasi" : angka(v);
      d.append(dt, dd);
      dl.append(d);
    }
  }
  // Arah yang akan direkam ditulis DI tombolnya: salah arah = pagar di sisi
  // yang salah. Tanpa arah (belum pernah jog) tombolnya redup.
  const arah = S.jog && S.jog.sisa ? S.jog.sisa[0] : null;
  const rekam = $("#tombol-rekam-batas");
  rekam.textContent = arah ? `Rekam batas: ${arah}` : "Rekam batas";
  rekam.disabled = !bisaGerak || !arah;

  // titik tertanda
  const kunciTitik = JSON.stringify(S.titik);
  if (cache.titik !== kunciTitik) {
    cache.titik = kunciTitik;
    const tb = $("#tabel-titik");
    const dicentang = new Set($$("#tabel-titik input:checked").map((c) => c.dataset.n));
    tb.innerHTML = "";
    S.titik.forEach((t, i) => {
      const tr = document.createElement("tr");
      const n = String(i + 1);
      const c = document.createElement("input");
      c.type = "checkbox";
      c.dataset.n = n;
      c.checked = dicentang.has(n);
      c.setAttribute("aria-label", `pilih titik ${n}`);
      c.addEventListener("change", perbaruiHapus);
      const td0 = document.createElement("td");
      td0.append(c);
      tr.append(td0);
      for (const v of [n, angka(t.x, 2), angka(t.y, 2), angka(t.z, 2), angka(t.e, 2), t.waktu || ""]) {
        const td = document.createElement("td");
        td.textContent = v;
        tr.append(td);
      }
      tb.append(tr);
    });
    $("#titik-kosong").hidden = S.titik.length > 0;
    perbaruiHapus();
  }
  $("#hapus-terakhir").disabled = !S.titik.length;
  $("#hapus-semua").disabled = !S.titik.length;
}

// ---------------------------------------------------------------- preset
const pilihPreset = new Set();     // id yang dicentang (urutan putar = urutan nomor)
const bukaPreset = new Set();      // id yang tabel langkahnya sedang dibuka
let kecepatanPutar = 40;

function teksLangkah(l) {
  if (l.jenis === "gerak") return `Gerak  X ${angka(l.x)}  Y ${angka(l.y)}  Z ${angka(l.z)}  E ${angka(l.e)}`;
  if (l.jenis === "vakum") return l.nyala ? "Vakum nyala" : "Vakum mati";
  if (l.jenis === "gripper") return l.buka ? "Gripper buka" : "Gripper genggam";
  if (l.jenis === "tunggu") return `Tunggu ${angka(l.detik)} detik`;
  return l.jenis;
}

function gambarPreset() {
  const bisaGerak = bolehGerak();
  const merekam = Array.isArray(S.rekam);
  const putar = S.putar;

  // --- rekam
  $("#rekam-awal").hidden = merekam;
  $("#rekam-mulai").disabled = !bisaGerak || !!putar;
  $("#rekam-aktif").hidden = !merekam;
  if (merekam) {
    $("#rekam-mode").textContent = S.rekam_otomatis
      ? "Merekam semua gerakan. Jog seperti biasa; Catat posisi tidak wajib."
      : "Merekam titik saja. Tekan Catat posisi di tiap posisi; lengan bergerak lurus di antaranya.";
    $("#rekam-kosong").textContent = S.rekam_otomatis
      ? "Mulai jog, gerakanmu tercatat di sini."
      : "Jog ke posisi pertama, lalu tekan Catat posisi.";
    const kunci = JSON.stringify(S.rekam);
    if (cache.rekam !== kunci) {
      cache.rekam = kunci;
      const ol = $("#rekam-langkah");
      ol.innerHTML = "";
      S.rekam.forEach((l) => {
        const li = document.createElement("li");
        li.textContent = teksLangkah(l);
        ol.append(li);
      });
      $("#rekam-kosong").hidden = S.rekam.length > 0;
    }
    $("#rekam-catat").disabled = !bisaGerak;
    $("#rekam-vakum").disabled = !bisaGerak;
    $("#rekam-vakum").setAttribute("aria-pressed", String(!!S.vakum));
    $("#rekam-undo").disabled = !S.rekam.length;
  } else {
    cache.rekam = null;
  }

  // --- daftar preset
  const ids = new Set(S.preset.map((p) => p.id));
  for (const id of [...pilihPreset]) if (!ids.has(id)) pilihPreset.delete(id);
  const kunciDaftar = JSON.stringify([S.preset, [...bukaPreset], bisaGerak, !!putar]);
  if (cache.preset !== kunciDaftar) {
    cache.preset = kunciDaftar;
    const ul = $("#daftar-preset");
    ul.innerHTML = "";
    S.preset.forEach((p, i) => {
      const li = document.createElement("li");
      const baris = document.createElement("div");
      baris.className = "baris-preset";

      const c = document.createElement("input");
      c.type = "checkbox";
      c.checked = pilihPreset.has(p.id);
      c.setAttribute("aria-label", `pilih preset ${i + 1}`);
      c.addEventListener("change", () => {
        if (c.checked) pilihPreset.add(p.id); else pilihPreset.delete(p.id);
        gambarPutar();
      });

      const nama = document.createElement("span");
      nama.className = "nama";
      nama.textContent = `${i + 1}. ${p.nama}`;

      const info = document.createElement("span");
      info.className = "info";
      const salah = p.status.findIndex((s) => !s.ok);
      info.textContent = `${p.langkah.length} langkah`;
      const lencana = document.createElement("span");
      lencana.className = salah < 0 ? "lencana-ok" : "lencana-salah";
      lencana.textContent = salah < 0 ? "bisa dijangkau" : `langkah ${salah + 1} tidak terjangkau`;

      const tombol = document.createElement("span");
      tombol.className = "baris-tombol";
      const lihat = document.createElement("button");
      lihat.textContent = bukaPreset.has(p.id) ? "Tutup" : "Lihat";
      lihat.className = "ghost";
      lihat.addEventListener("click", () => {
        if (bukaPreset.has(p.id)) bukaPreset.delete(p.id); else bukaPreset.add(p.id);
        gambarPreset();
      });
      const coba = document.createElement("button");
      coba.textContent = "Coba sekali";
      coba.disabled = !bisaGerak || !!putar || salah >= 0;
      coba.addEventListener("click", async () => {
        const ok = await tanya({
          judul: `Coba "${p.nama}" sekali?`,
          isi: "Lengan naik ke tinggi aman, pindah ke langkah pertama, lalu menjalankan semua langkah pelan (20 mm/s).",
          cek: ["Jalur lengan bersih dari benda dan tangan"],
          ya: "Coba sekali",
        });
        if (ok) kirimPutar("preset_coba", { id: p.id });
      });
      tombol.append(lihat, coba);
      baris.append(c, nama, info, lencana, tombol);
      li.append(baris);

      if (bukaPreset.has(p.id)) {
        const ol = document.createElement("ol");
        ol.className = "daftar-langkah rinci";
        p.langkah.forEach((l, n) => {
          const item = document.createElement("li");
          const s = p.status[n];
          if (!s.ok) item.className = "salah";
          const teks = document.createElement("span");
          teks.textContent = teksLangkah(l) + (s.ok ? "" : `  (${s.alasan})`);
          item.append(teks);
          if (l.jenis === "gerak") {
            const ke = document.createElement("button");
            ke.textContent = "Ke sini";
            ke.className = "ghost kecil";
            ke.disabled = !bisaGerak || !!putar || !s.ok;
            ke.addEventListener("click", () => kirimPutar("preset_ke_langkah", { id: p.id, nomor: n + 1 }));
            item.append(ke);
          }
          ol.append(item);
        });
        li.append(ol);
      }
      ul.append(li);
    });
    $("#preset-kosong").hidden = S.preset.length > 0;
  }
  $("#preset-hapus-centang").disabled = !pilihPreset.size;
  $("#preset-hapus-terakhir").disabled = !S.preset.length;
  $("#preset-hapus-semua").disabled = !S.preset.length;

  // --- sampah
  const kunciSampah = JSON.stringify(S.sampah);
  if (cache.sampah !== kunciSampah) {
    cache.sampah = kunciSampah;
    $("#sampah-judul").textContent = `Sampah (${S.sampah.length})`;
    const ul = $("#daftar-sampah");
    ul.innerHTML = "";
    S.sampah.forEach((p) => {
      const li = document.createElement("li");
      const t = document.createElement("span");
      t.textContent = `${p.nama}  (${p.jumlah} langkah, dihapus ${p.dihapus})`;
      const b = document.createElement("button");
      b.textContent = "Pulihkan";
      b.className = "ghost kecil";
      b.addEventListener("click", () => kirim("preset_pulihkan", { id: p.id }));
      li.append(t, b);
      ul.append(li);
    });
    $("#sampah-kosongkan").disabled = !S.sampah.length;
  }

  gambarPutar();
}

function gambarPutar() {
  if (!S) return;
  const putar = S.putar;
  const terpilih = S.preset.filter((p) => pilihPreset.has(p.id));
  $("#putar-pilihan").textContent = terpilih.length
    ? "Akan diputar: " + terpilih.map((p) => `${S.preset.indexOf(p) + 1}. ${p.nama}`).join(", ")
    : "Centang preset di daftar untuk diputar.";

  const seg = $("#putar-kecepatan");
  if (seg.childElementCount !== S.kecepatan_preset.length) {
    seg.innerHTML = "";
    S.kecepatan_preset.forEach((v) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = String(v);
      b.dataset.v = String(v);
      b.addEventListener("click", () => { kecepatanPutar = v; gambarPutar(); });
      seg.append(b);
    });
  }
  [...seg.children].forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.v) === kecepatanPutar)));

  const terus = $("#putar-terus").checked;
  $("#putar-ulang").disabled = terus || !!putar;
  const semuaOk = terpilih.length > 0 && terpilih.every((p) => p.ok);
  const dijeda = putar && putar.keadaan === "jeda";
  const jalan = $("#putar-jalan");
  jalan.textContent = dijeda ? "Lanjut" : "Jalan";
  // Tombol yang tidak relevan diredupkan (kesepakatan rancangan).
  jalan.disabled = dijeda ? false : (!!putar || !bolehGerak() || !semuaOk || Array.isArray(S.rekam));
  $("#putar-jeda").disabled = !putar || dijeda;
  $("#putar-berhenti").disabled = !putar;

  const k = $("#putar-kemajuan");
  if (putar) {
    const total = putar.total ? `/${putar.total}` : " (terus)";
    k.textContent = `${dijeda ? "Dijeda" : "Berjalan"}: putaran ${putar.putaran}${total}, ${putar.nama}, langkah ${putar.langkah_ke}/${putar.total_langkah}`;
  } else if (terpilih.length && !semuaOk) {
    k.textContent = "Ada preset terpilih yang punya langkah tidak terjangkau. Buka Lihat untuk detailnya.";
  } else {
    k.textContent = "";
  }
}

function perbaruiHapus() {
  $("#hapus-centang").disabled = !$$("#tabel-titik input:checked").length;
}

// ---------------------------------------------------------------- tombol
function pasangTombol() {
  const bukaTab = (nama) => {
    $$("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === nama)));
    $$(".isi-tab").forEach((t) => { t.hidden = t.id !== `tab-${nama}`; });
  };
  $$("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    bukaTab(b.dataset.tab);
    history.replaceState(null, "", `#${b.dataset.tab}`);
  }));
  // Alamat #ukur langsung membuka tab Ukur (bisa di-bookmark, dan bertahan saat reload).
  const awal = location.hash.slice(1);
  if ($(`[data-tab="${awal}"]`)) bukaTab(awal);

  $$("[data-aksi]").forEach((b) => b.addEventListener("click", () => {
    const isi = b.dataset.isi ? JSON.parse(b.dataset.isi) : {};
    kirim(b.dataset.aksi, isi);
  }));

  $("#tombol-berhenti").addEventListener("click", berhenti);
  $("#tombol-panduan").addEventListener("click", bukaPanduan);
  $("#tutup-panduan").addEventListener("click", () => $("#panduan").close());
  $("#tombol-vakum").addEventListener("click", () => kirim("vakum", { nyala: !S.vakum }));

  $("#form-sambung").addEventListener("submit", (e) => {
    e.preventDefault();
    kirim("sambung", { port: $("#isian-port").value.trim() || null, sim: $("#isian-sim").checked });
  });

  $("#tombol-homing").addEventListener("click", () => kirim("homing"));

  $("#tombol-homing-ulang").addEventListener("click", async () => {
    const ok = await tanya({
      judul: "Kalibrasi ulang robot?",
      isi: "Keempat sumbu bergerak sendiri mencari titik nol. Firmware melepas motor sebentar di awal homing, jadi lengan yang sedang tinggi bisa turun.",
      cek: CEK_HOMING,
      ya: "Mulai homing",
    });
    if (ok) kirim("homing");
  });

  $("#tombol-matikan").addEventListener("click", async () => {
    const ok = await tanya({
      judul: "Matikan robot?",
      isi: "Lengan pulang ke home, lalu motor dilepas dan lengan jadi loyo. Setelah ini saklar 12V aman dimatikan. Untuk gerak lagi perlu homing.",
      cek: ["Pegang lengan robot"],
      ya: "Matikan motor",
      bahaya: true,
    });
    if (ok) kirim("matikan", { dipegang: true });
  });

  // --- ukur: reset
  $("#ukur-reset").addEventListener("click", async () => {
    const ok = await tanya({
      judul: "Kembalikan semua angka ukur ke angka awal?",
      isi: "Z belt, Z bak, posisi kedua bak, dan titik parkir kembali ke angka awal. Angka yang sudah diukur hilang.",
      ya: "Kembalikan",
      bahaya: true,
    });
    if (ok) kirim("ukur_reset");
  });

  // --- preset: rekam
  $("#rekam-mulai").addEventListener("click", () =>
    kirim("rekam_mulai", { otomatis: $("#rekam-otomatis").checked }));
  $("#rekam-catat").addEventListener("click", () => kirim("rekam_catat"));
  $("#rekam-vakum").addEventListener("click", () => kirim("vakum", { nyala: !S.vakum }));
  $("#rekam-tunggu").addEventListener("click", () => {
    const detik = Number($("#rekam-detik").value);
    kirim("rekam_tunggu", { detik });
  });
  $("#rekam-undo").addEventListener("click", () => kirim("rekam_hapus_langkah"));
  $("#form-rekam").addEventListener("submit", async (e) => {
    e.preventDefault();
    const j = await kirim("rekam_simpan", { nama: $("#rekam-nama").value });
    if (j.ok) $("#rekam-nama").value = "";
  });
  $("#rekam-batal").addEventListener("click", async () => {
    if (S.rekam && S.rekam.length) {
      const ok = await tanya({ judul: "Buang rekaman ini?", isi: `${S.rekam.length} langkah yang belum disimpan akan hilang.`, ya: "Buang", bahaya: true });
      if (!ok) return;
    }
    kirim("rekam_batal");
  });

  // --- preset: hapus dan sampah
  $("#preset-hapus-centang").addEventListener("click", () => {
    if (pilihPreset.size) kirim("preset_hapus", { ids: [...pilihPreset] });
  });
  $("#preset-hapus-terakhir").addEventListener("click", () => kirim("preset_hapus", { terakhir: true }));
  $("#preset-hapus-semua").addEventListener("click", async () => {
    const ok = await tanya({
      judul: `Hapus semua ${S.preset.length} preset?`,
      isi: "Semua preset pindah ke Sampah dan masih bisa dipulihkan sampai sampahnya dikosongkan.",
      ya: "Hapus semua",
      bahaya: true,
    });
    if (ok) kirim("preset_hapus", { semua: true });
  });
  $("#sampah-kosongkan").addEventListener("click", async () => {
    const ok = await tanya({
      judul: "Kosongkan sampah?",
      isi: `${S.sampah.length} preset di sampah dihapus permanen. Tidak bisa dibatalkan.`,
      ya: "Kosongkan",
      bahaya: true,
    });
    if (ok) kirim("preset_kosongkan_sampah");
  });

  // --- preset: putar
  $("#putar-terus").addEventListener("change", gambarPutar);
  $("#putar-jalan").addEventListener("click", async () => {
    if (S.putar && S.putar.keadaan === "jeda") {
      kirim("preset_lanjut");
      return;
    }
    const ids = S.preset.filter((p) => pilihPreset.has(p.id)).map((p) => p.id);
    const terus = $("#putar-terus").checked;
    const ulang = terus ? 0 : Math.max(1, Math.floor(Number($("#putar-ulang").value) || 1));
    const ok = await tanya({
      judul: terus ? "Putar terus sampai dihentikan?" : `Putar ${ulang} kali?`,
      isi: `${ids.length} preset, kecepatan ${kecepatanPutar} mm/s. Hentikan kapan saja dengan Jeda, Berhenti, atau Spasi.`,
      cek: ["Jalur lengan bersih dari benda dan tangan"],
      ya: "Jalan",
    });
    if (ok) kirimPutar("preset_jalan", { ids, ulang, kecepatan: kecepatanPutar });
  });
  $("#putar-jeda").addEventListener("click", () => kirim("preset_jeda"));
  $("#putar-berhenti").addEventListener("click", () => kirim("preset_berhenti"));

  $("#hapus-centang").addEventListener("click", () => {
    const nomor = $$("#tabel-titik input:checked").map((c) => Number(c.dataset.n));
    if (nomor.length) kirim("hapus_titik", { nomor });
  });
  $("#hapus-terakhir").addEventListener("click", () => kirim("hapus_titik", { terakhir: true }));
  $("#hapus-semua").addEventListener("click", async () => {
    const ok = await tanya({
      judul: `Hapus semua ${S.titik.length} titik?`,
      isi: "Titik tertanda dihapus dari titik-tanda.json. Tidak bisa dibatalkan.",
      ya: "Hapus semua",
      bahaya: true,
    });
    if (ok) kirim("hapus_titik", { semua: true });
  });
}

pasangTombol();
pasangJog();
pasangKeyboard();
tarik();
