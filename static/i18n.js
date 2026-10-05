/* EventMap — textes de l'interface dynamique (fr / en / ar).
   Même vocabulaire que i18n_server.py (titres, aperçus de lien). L'arabe est du
   texte rédigé à la main, pas une traduction automatique : il reste à faire relire
   par un locuteur natif avant d'être promu (docs/multi-ville.md).
   La langue vient de <html lang>, posée par le serveur selon l'URL. */
window.EM_I18N = (() => {
  const D = {
    fr: {
      tonight_in: "Ce soir à {city}", now: "Maintenant", today: "Ce soir", tomorrow: "Demain",
      weekend: "Week-end", week: "7 jours", near_me: "📍 Autour de moi", free: "Gratuit", paid: "Payant",
      free_cond: "Gratuit*", price_all: "Gratuit ou payant", cat_all: "Toutes catégories", categories: "Catégories",
      distance: "Distance", price: "Prix", all_city: "Toute la ville", km: "km",
      loading: "Chargement…", n_events: "{n} événement{s}", n_events_in: "{n} événement{s} à moins de {r} km",
      none_title: "Rien dans ce rayon", none_hint: "Élargis la distance ou regarde demain.",
      none_city: "Aucun événement ne correspond pour le moment.",
      error_network: "Impossible de charger les événements. Vérifie ta connexion.", retry: "Réessayer",
      geo_locating: "Localisation…", geo_denied: "Localisation refusée — position par défaut conservée.",
      geo_unavailable: "Localisation indisponible — position par défaut conservée.",
      partial: "Certaines sources sont en retard : les événements affichés sont les derniers connus.",
      stale: "Les données ne sont plus à jour : ce qui s'affiche peut être périmé. Ce n'est pas une absence d'événements.",
      no_sources: "Aucune source de données n'est active pour cette ville.",
      empty_but_degraded: "Aucun événement à afficher, mais les sources sont en retard : cela ne veut pas dire qu'il n'y a rien.",
      directions: "Itinéraire", official: "Page officielle", book: "Réserver", details: "Détails", close: "Fermer",
      share: "Partager", copied: "Lien copié", source: "Source", updated: "Mis à jour", from_price: "à partir de {p}",
      sheet_expand: "Agrandir la liste", sheet_collapse: "Réduire la liste", list: "Liste des événements",
      map: "Carte des événements", city: "Ville", switch_city: "Changer de ville", stale_item: "donnée ancienne",
      cancelled: "Annulé", postponed: "Reporté", dates_more: "+{n} date{s}", lang_switch: "Langue",
      suggest_city: "Vous êtes à {city} ?", yes_go: "Aller à {city}", dismiss: "Non merci",
      cat: { music: "Musique", theatre: "Spectacle", cinema: "Cinéma", expo: "Expo", kids: "Enfants",
             workshop: "Atelier", talk: "Rencontre", sport: "Sport", market: "Marché / festival", other: "Autre" },
    },
    en: {
      tonight_in: "Tonight in {city}", now: "Now", today: "Tonight", tomorrow: "Tomorrow",
      weekend: "This weekend", week: "7 days", near_me: "📍 Near me", free: "Free", paid: "Paid",
      free_cond: "Free*", price_all: "Free or paid", cat_all: "All categories", categories: "Categories",
      distance: "Distance", price: "Price", all_city: "Whole city", km: "km",
      loading: "Loading…", n_events: "{n} event{s}", n_events_in: "{n} event{s} within {r} km",
      none_title: "Nothing in this radius", none_hint: "Widen the distance or look at tomorrow.",
      none_city: "No event matches right now.",
      error_network: "Couldn't load events. Check your connection.", retry: "Try again",
      geo_locating: "Locating…", geo_denied: "Location denied — keeping the default position.",
      geo_unavailable: "Location unavailable — keeping the default position.",
      partial: "Some sources are running late: the events shown are the last known ones.",
      stale: "The data is out of date: what you see may be obsolete. This is not the same as there being no events.",
      no_sources: "No data source is active for this city.",
      empty_but_degraded: "No event to show, but the sources are running late — that doesn't mean nothing is on.",
      directions: "Directions", official: "Official page", book: "Book", details: "Details", close: "Close",
      share: "Share", copied: "Link copied", source: "Source", updated: "Updated", from_price: "from {p}",
      sheet_expand: "Expand the list", sheet_collapse: "Collapse the list", list: "Event list",
      map: "Event map", city: "City", switch_city: "Change city", stale_item: "old data",
      cancelled: "Cancelled", postponed: "Postponed", dates_more: "+{n} date{s}", lang_switch: "Language",
      suggest_city: "Are you in {city}?", yes_go: "Go to {city}", dismiss: "No thanks",
      cat: { music: "Concerts", theatre: "Shows", cinema: "Cinema", expo: "Exhibitions", kids: "Kids",
             workshop: "Workshops", talk: "Talks", sport: "Sports", market: "Markets & festivals", other: "Experiences" },
      grp: { concerts: "Concerts", "culture-art": "Culture & Art", cinema: "Cinema", sports: "Sports",
             "food-markets": "Food & Markets", workshops: "Workshops", family: "Family", experiences: "Experiences" },
    },
    ar: {
      tonight_in: "هذا المساء في {city}", now: "الآن", today: "هذا المساء", tomorrow: "غدًا",
      weekend: "نهاية الأسبوع", week: "٧ أيام", near_me: "📍 بالقرب مني", free: "مجاني", paid: "مدفوع",
      free_cond: "مجاني*", price_all: "مجاني أو مدفوع", cat_all: "كل الفئات", categories: "الفئات",
      distance: "المسافة", price: "السعر", all_city: "كل المدينة", km: "كم",
      loading: "جارٍ التحميل…", n_events: "{n} فعالية", n_events_in: "{n} فعالية ضمن {r} كم",
      none_title: "لا شيء ضمن هذه المسافة", none_hint: "وسّع المسافة أو انظر إلى الغد.",
      none_city: "لا توجد فعالية مطابقة حاليًا.",
      error_network: "تعذّر تحميل الفعاليات. تحقق من اتصالك.", retry: "أعد المحاولة",
      geo_locating: "جارٍ تحديد الموقع…", geo_denied: "تم رفض تحديد الموقع — بقي الموقع الافتراضي.",
      geo_unavailable: "تحديد الموقع غير متاح — بقي الموقع الافتراضي.",
      partial: "بعض المصادر متأخرة: الفعاليات المعروضة هي آخر ما هو معروف.",
      stale: "البيانات غير محدّثة: قد يكون ما تراه قديمًا. هذا لا يعني أنه لا توجد فعاليات.",
      no_sources: "لا يوجد مصدر بيانات نشط لهذه المدينة.",
      empty_but_degraded: "لا توجد فعاليات للعرض، لكن المصادر متأخرة — وهذا لا يعني أنه لا يوجد شيء.",
      directions: "الاتجاهات", official: "الصفحة الرسمية", book: "احجز", details: "التفاصيل", close: "إغلاق",
      share: "مشاركة", copied: "تم نسخ الرابط", source: "المصدر", updated: "آخر تحديث", from_price: "ابتداءً من {p}",
      sheet_expand: "توسيع القائمة", sheet_collapse: "طيّ القائمة", list: "قائمة الفعاليات",
      map: "خريطة الفعاليات", city: "المدينة", switch_city: "تغيير المدينة", stale_item: "بيانات قديمة",
      cancelled: "أُلغيت", postponed: "مؤجلة", dates_more: "+{n} موعد", lang_switch: "اللغة",
      suggest_city: "هل أنت في {city}؟", yes_go: "اذهب إلى {city}", dismiss: "لا، شكرًا",
      cat: { music: "حفلات", theatre: "عروض", cinema: "سينما", expo: "معارض", kids: "أطفال",
             workshop: "ورش عمل", talk: "لقاءات", sport: "رياضة", market: "أسواق ومهرجانات", other: "تجارب" },
      grp: { concerts: "حفلات", "culture-art": "ثقافة وفن", cinema: "سينما", sports: "رياضة",
             "food-markets": "طعام وأسواق", workshops: "ورش عمل", family: "عائلة", experiences: "تجارب" },
    },
  };

  const LOCALE = { fr: "fr-FR", en: "en-GB", ar: "ar-SA-u-nu-latn-ca-gregory" };
  const lang = (document.documentElement.lang || "en").slice(0, 2);
  const dict = D[lang] || D.en;

  /* t("n_events_in", {n: 3, r: 2}) — {s} = « s » du pluriel (fr/en seulement). */
  function t(key, vars = {}) {
    let s = dict[key] ?? D.en[key] ?? key;
    if (typeof s !== "string") return key;
    const v = { s: vars.n > 1 ? "s" : "", ...vars };
    return s.replace(/\{(\w+)\}/g, (_, k) => (v[k] ?? ""));
  }
  const catLabel = (k) => (dict.cat && dict.cat[k]) || D.en.cat[k] || k;
  const grpLabel = (k) => (dict.grp && dict.grp[k]) || D.en.grp?.[k] || (dict.cat && dict.cat[k]) || k;

  return { lang, dir: lang === "ar" ? "rtl" : "ltr", locale: LOCALE[lang] || "en-GB", t, catLabel, grpLabel };
})();
