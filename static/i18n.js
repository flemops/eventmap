/* EventMap — textes de l'interface dynamique (fr / en / ar).
   Même vocabulaire que i18n_server.py (titres, aperçus de lien). L'arabe est du
   texte rédigé à la main, pas une traduction automatique : il reste à faire relire
   par un locuteur natif avant d'être promu (docs/multi-ville.md).
   La langue vient de <html lang>, posée par le serveur selon l'URL. */
window.EM_I18N = (() => {
  const D = {
    fr: {
      tonight_in: "Ce soir à {city}", now: "Maintenant", today: "Ce soir", tomorrow: "Demain",
      weekend: "Week-end", week: "7 jours", near_me: "Autour de moi", free: "Gratuit", paid: "Payant",
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
      filters: "Filtres", reset: "Réinitialiser", all: "Tous", when_l: "Quand", weekend_s: "Week-end",
      show_n: "Voir {n} événement{s}", show_none: "Aucun événement", back: "Retour", locate: "Autour de moi", recenter: "Recentrer",
      search_area: "Rechercher dans cette zone", m: "m",
      top_picks: "Sélection de ce soir", all_events: "Tous les événements",
      when_phrase: { today: "ce soir", tomorrow: "demain", weekend: "ce week-end", week: "cette semaine" },
      no_free: "Aucun événement gratuit {when}", show_all: "Voir tous les événements",
      nothing_near: "Rien à proximité", expand_to: "Élargir à {r} km", expand_city: "Voir toute la ville",
      none_when: "Aucun événement {when}", see_next: { tomorrow: "Voir demain", weekend: "Voir le week-end", week: "Voir les 7 jours" },
      degraded_title: "Les sources sont en retard", unavailable_title: "Impossible de charger les événements",
      save: "Enregistrer", saved: "Enregistré", unsave: "Retirer des favoris", saved_view: "Mes événements enregistrés",
      saved_empty: "Rien d'enregistré", saved_hint: "Touche « Enregistrer » sur un événement pour le retrouver ici.",
      saved_gone: "Cet événement n'est plus au programme.", back_list: "Retour à la liste", n_saved: "{n} enregistré{s}",
      copy_failed: "Copie impossible", filter_active: "{n} filtre{s} actif{s}",
      in_city: { today: "Ce soir à {city}", tomorrow: "Demain à {city}", weekend: "Ce week-end à {city}", week: "7 jours à {city}" },
      cat: { music: "Musique", theatre: "Spectacle", cinema: "Cinéma", expo: "Expo", kids: "Enfants",
             workshop: "Atelier", talk: "Rencontre", sport: "Sport", market: "Marché / festival", other: "Autre" },
    },
    en: {
      tonight_in: "Tonight in {city}", now: "Now", today: "Tonight", tomorrow: "Tomorrow",
      weekend: "This weekend", week: "7 days", near_me: "Near me", free: "Free", paid: "Paid",
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
      filters: "Filters", reset: "Reset", all: "All", when_l: "When", weekend_s: "Weekend",
      show_n: "Show {n} event{s}", show_none: "No events", back: "Back", locate: "Near me", recenter: "Recenter",
      search_area: "Search this area", m: "m",
      top_picks: "Top picks tonight", all_events: "All events",
      when_phrase: { today: "tonight", tomorrow: "tomorrow", weekend: "this weekend", week: "this week" },
      no_free: "No free events {when}", show_all: "Show all events",
      nothing_near: "Nothing nearby", expand_to: "Expand to {r} km", expand_city: "See the whole city",
      none_when: "No events {when}", see_next: { tomorrow: "See tomorrow", weekend: "See the weekend", week: "See the next 7 days" },
      degraded_title: "Sources are running late", unavailable_title: "Couldn't load events",
      save: "Save", saved: "Saved", unsave: "Remove from saved", saved_view: "Saved events",
      saved_empty: "Nothing saved yet", saved_hint: "Tap Save on an event to keep it here.",
      saved_gone: "This event is no longer on.", back_list: "Back to the list", n_saved: "{n} saved",
      copy_failed: "Couldn't copy", filter_active: "{n} active filter{s}",
      in_city: { today: "Tonight in {city}", tomorrow: "Tomorrow in {city}", weekend: "This weekend in {city}", week: "Next 7 days in {city}" },
      cat: { music: "Concerts", theatre: "Shows", cinema: "Cinema", expo: "Exhibitions", kids: "Kids",
             workshop: "Workshops", talk: "Talks", sport: "Sports", market: "Markets & festivals", other: "Experiences" },
      grp: { concerts: "Concerts", "culture-art": "Culture & Art", cinema: "Cinema", sports: "Sports",
             "food-markets": "Food & Markets", workshops: "Workshops", family: "Family", experiences: "Experiences" },
    },
    ar: {
      tonight_in: "هذا المساء في {city}", now: "الآن", today: "هذا المساء", tomorrow: "غدًا",
      weekend: "نهاية الأسبوع", week: "٧ أيام", near_me: "بالقرب مني", free: "مجاني", paid: "مدفوع",
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
      filters: "تصفية", reset: "إعادة ضبط", all: "الكل", when_l: "متى", weekend_s: "نهاية الأسبوع",
      show_n: "عرض {n} فعالية", show_none: "لا فعاليات", back: "رجوع", locate: "بالقرب مني", recenter: "إعادة التمركز",
      search_area: "ابحث في هذه المنطقة", m: "م",
      top_picks: "اختيارات هذا المساء", all_events: "كل الفعاليات",
      when_phrase: { today: "هذا المساء", tomorrow: "غدًا", weekend: "في نهاية الأسبوع", week: "هذا الأسبوع" },
      no_free: "لا توجد فعاليات مجانية {when}", show_all: "عرض كل الفعاليات",
      nothing_near: "لا شيء بالقرب", expand_to: "وسّع إلى {r} كم", expand_city: "عرض كل المدينة",
      none_when: "لا توجد فعاليات {when}", see_next: { tomorrow: "عرض الغد", weekend: "عرض نهاية الأسبوع", week: "عرض ٧ أيام" },
      degraded_title: "بعض المصادر متأخرة", unavailable_title: "تعذّر تحميل الفعاليات",
      save: "حفظ", saved: "تم الحفظ", unsave: "إزالة من المحفوظات", saved_view: "الفعاليات المحفوظة",
      saved_empty: "لا شيء محفوظ بعد", saved_hint: "اضغط «حفظ» على فعالية لتجدها هنا.",
      saved_gone: "لم تعد هذه الفعالية متاحة.", back_list: "العودة إلى القائمة", n_saved: "{n} محفوظ",
      copy_failed: "تعذّر النسخ", filter_active: "{n} تصفية نشطة",
      in_city: { today: "هذا المساء في {city}", tomorrow: "غدًا في {city}", weekend: "نهاية الأسبوع في {city}", week: "٧ أيام في {city}" },
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

  /* Entrée structurée (when_phrase, see_next) : renvoie l'objet, pas une chaîne. */
  const raw = (key, sub) => { const v = dict[key] ?? D.en[key]; return sub == null ? v : (v && (v[sub] ?? D.en[key]?.[sub])); };

  return { lang, dir: lang === "ar" ? "rtl" : "ltr", locale: LOCALE[lang] || "en-GB", t, raw, catLabel, grpLabel };
})();
