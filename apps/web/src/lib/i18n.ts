/**
 * UI strings. Uzbek (Latin) is the default and currently the only complete locale. Keys are
 * grouped by area; add `ru` / `en` dictionaries with the same shape when needed.
 */
export const uz = {
  app: {
    name: "AI Davomat",
    tagline: "Xodimlar davomati boshqaruvi",
  },
  nav: {
    dashboard: "Bosh sahifa",
    employees: "Xodimlar",
    departments: "Boʻlimlar",
    attendance: "Davomat",
    attendanceHistory: "Davomat tarixi",
    recognition: "AI tanish",
    schedules: "Ish jadvallari",
    cameras: "Kameralar",
    reports: "Hisobotlar",
    users: "Administratorlar",
    settings: "Sozlamalar",
    system: "Tizim holati",
    branches: "Filiallar",
    designSystem: "Dizayn tizimi",
    sectionMain: "Asosiy",
    sectionOrg: "Tashkilot",
    sectionSystem: "Tizim",
  },
  auth: {
    title: "Tizimga kirish",
    subtitle: "Hisobingiz maʼlumotlarini kiriting",
    username: "Foydalanuvchi nomi",
    password: "Parol",
    showPassword: "Parolni koʻrsatish",
    hidePassword: "Parolni yashirish",
    submit: "Kirish",
    submitting: "Tekshirilmoqda…",
    invalid: "Foydalanuvchi nomi yoki parol notoʻgʻri.",
    tooMany: (minutes: number) =>
      `Juda koʻp urinish. ${minutes} daqiqadan keyin qayta urinib koʻring.`,
    network: "Server bilan aloqa yoʻq. Internet yoki serverni tekshiring.",
    sessionEnded: "Sessiya tugadi. Iltimos, qayta kiring.",
    logout: "Chiqish",
    checking: "Sessiya tekshirilmoqda…",
  },
  roles: {
    SUPER_ADMIN: "Bosh administrator",
    ADMIN: "Administrator",
    VIEWER: "Kuzatuvchi",
  } as Record<string, string>,
  common: {
    retry: "Qayta urinish",
    cancel: "Bekor qilish",
    close: "Yopish",
    save: "Saqlash",
    loading: "Yuklanmoqda…",
    empty: "Maʼlumot yoʻq",
    errorTitle: "Xatolik yuz berdi",
    errorBody: "Maʼlumotni yuklab boʻlmadi.",
    forbidden: "Bu boʻlimga ruxsatingiz yoʻq.",
    notFound: "Sahifa topilmadi",
    backHome: "Bosh sahifaga qaytish",
    menu: "Menyu",
    devPlaceholder: "ISHLAB CHIQILMOQDA",
  },
  dashboard: {
    title: "Bosh sahifa",
    today: "Bugungi davomat",
    employees: "Xodimlar soni",
    present: "Kelganlar",
    late: "Kechikkanlar",
    absent: "Kelmaganlar",
    pending: "Hali kelmagan",
    incomplete: "Chiqish qayd etilmagan",
    weekend: "Dam olish kuni",
    notScheduled: "Jadvalsiz",
    lateList: "Bugun kechikkanlar",
    noLate: "Bugun kechikkanlar yoʻq.",
    noAccess: "Davomatni koʻrish uchun ruxsat yoʻq.",
  },
} as const;

export type Messages = typeof uz;
export const t = uz;
