import type { BadgeTone } from "@/components/ui/badge";

export type HealthIssue = { code: string; severity: "bad" | "warn" | "tip"; [k: string]: unknown };
export type CameraHealth = { verdict: "good" | "warn" | "bad"; issues: HealthIssue[] };

export const HEALTH_VERDICT: Record<CameraHealth["verdict"], { label: string; tone: BadgeTone }> = {
  good: { label: "Yaxshi", tone: "success" },
  warn: { label: "Diqqat", tone: "warning" },
  bad: { label: "Ishlamayapti", tone: "danger" },
};

/** Plain-language advice for each finding (codes from apps/camera/camera/health.py). */
export function healthAdvice(i: HealthIssue): { title: string; fix: string } {
  const n = (k: string) => String(i[k] ?? "?");
  switch (i.code) {
    case "offline":
      return { title: "Kameraga ulanib boʻlmayapti", fix: "Kamera yoqilganini, tarmoq kabelini, IP manzil va parolni tekshiring." };
    case "unstable_connection":
      return { title: `Ulanish tez-tez uziladi (${n("count")} marta / soat)`, fix: "Kamerani kabel bilan ulang yoki kamera sozlamalarida bitreytni kamaytiring (masalan, 2048 Kbps)." };
    case "frame_loss":
      return { title: `Kadrlar yoʻqolyapti (${n("count")} ta / 5 daqiqa)`, fix: "Tarmoq yetishmayapti: kabel bilan ulang yoki kamera sozlamalarida bitreyt va kadr tezligini kamaytiring." };
    case "low_fps":
      return { title: `Kadr tezligi past (${n("fps")} kadr/s)`, fix: "Kamera sozlamalarida kadr tezligini 10–15 ga qoʻying va tarmoqni tekshiring." };
    case "cpu_high":
      return { title: `Kompyuterni koʻp yuklayapti (${n("cpu")}% bir yadro)`, fix: "Ruxsatni 1920×1080 ga, kadr tezligini 15 ga tushiring, kodekni H.264 qiling." };
    case "h265":
      return { title: "Kodek H.265", fix: "Kamera sozlamalarida (Video → Video Encoding) H.264 ni tanlang: uni ochish ~2 baravar yengil." };
    case "resolution_high":
      return { title: `Ruxsat keragidan katta (${n("width")}×${n("height")})`, fix: "Davomat uchun 1920×1080 yetarli: kamera sozlamalarida ruxsatni pasaytiring." };
    case "resolution_low":
      return { title: `Ruxsat past (${n("width")}×${n("height")})`, fix: "Uzoqdan yuzlar kichik chiqadi: asosiy oqimdan foydalaning yoki ruxsatni 1920×1080 ga koʻtaring." };
    case "fps_high":
      return { title: `Kadr tezligi yuqori (${n("fps")} kadr/s)`, fix: "Davomat uchun 15 kadr/s yetarli: pasaytirsangiz kompyuter yengillashadi." };
    default:
      return { title: i.code, fix: "" };
  }
}
