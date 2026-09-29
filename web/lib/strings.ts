import type { Language } from "./languages";

/**
 * Every string the frontend itself contributes to the page (as opposed to
 * text the API returns, which is already localised server-side -- see
 * `openlaw.answers.escalation`'s Hindi dict and `openlaw.api.refusal
 * .refusal_detail`). Task 6 review, H3: selecting Hindi previously changed
 * only the answer body: every label, button, placeholder and error message
 * around it stayed English. This is the one place all of that text lives,
 * keyed by language, so nothing added later can accidentally reintroduce an
 * English-only string next to Hindi content.
 *
 * Only `en`/`hi` -- the two languages this product offers
 * (`lib/languages.ts`) -- so there is no "missing translation" fallback
 * logic to get wrong.
 *
 * **Adding a string means adding it to BOTH dictionaries.** `UIStrings` is
 * the enforcement: a key added to `EN` alone will not compile. The Hindi
 * here is best-effort pending a native-speaker review pass, the same caveat
 * `lib/fallbackHelplines.ts` carries.
 */
export interface UIStrings {
  tagline: string;
  intro: string;
  languageLabel: string;
  questionLabel: string;
  questionPlaceholder: string;
  askButton: string;
  askingButton: string;
  thinkingStatus: string;
  genericError: string;
  timeoutError: string;
  sourcesHeading: string;
  corpusNote: (corpusVersion: string) => string;
  unanswerableSuffix: string;
  quotaRemainingLabel: string;
  resetsOnLabel: string;
  signupCta: string;
  noScriptNotice: string;
  previousAnswerNotice: string;
  helplineHeading: string;
  helplineNote: string;
  /** Heading above the crisis block, so the alert is not a wall of prose. */
  crisisHeading: string;
  welcomeHeading: string;
  /** Short, honest claims about what this is. Rendered as a list. */
  welcomePoints: readonly string[];
  coverageNote: string;
  examplesHeading: string;
  /** Tappable starter questions. Must not read as legal advice. */
  exampleQuestions: readonly string[];
  answerHeading: string;
  /** Names the boundary between the law and this system's own framing. */
  answerFramingNote: string;
  sourceKindStatute: string;
  sourceKindJudgment: string;
  sourceKindDocument: string;
  /** Secondary line under a citation, naming the host it opens. */
  sourceOpensAt: (host: string) => string;
  notLegalAdvice: string;
}

const EN: UIStrings = {
  tagline: "Free answers about Indian law, with the government source attached.",
  intro:
    "Ask a question about Indian law and get an answer grounded in a government source you can check yourself. This is legal information, not legal advice. No account needed.",
  languageLabel: "Language / भाषा",
  questionLabel: "Your question",
  questionPlaceholder: "e.g. How much notice must my landlord give before eviction?",
  askButton: "Ask",
  askingButton: "Asking…",
  thinkingStatus: "Looking through the law. This can take up to half a minute.",
  genericError: "Something went wrong. Please try again.",
  timeoutError: "The request took too long. Please check your connection and try again.",
  sourcesHeading: "Where this comes from",
  corpusNote: (corpusVersion) =>
    `Based on the ${corpusVersion} snapshot of Indian law. Always check the official source before relying on this.`,
  unanswerableSuffix: "You can still get help from a free legal aid lawyer.",
  quotaRemainingLabel: "Free questions left",
  resetsOnLabel: "Resets on",
  signupCta: "Create a free account for more questions",
  noScriptNotice:
    "This works best with JavaScript enabled. Without it, your question is kept if the page reloads, but you will need to press Ask again.",
  previousAnswerNotice: "Could not get a new answer. Your previous answer is still shown below.",
  helplineHeading: "Need help right now?",
  helplineNote: "Free to call, day or night, from any phone in India.",
  crisisHeading: "Help is available right now",
  welcomeHeading: "What this is",
  welcomePoints: [
    "Free to use, with no account and no sign-in.",
    "Every answer is built only from law we can cite, and every source is a link you can open yourself.",
    "It is not a lawyer and cannot give legal advice. For that, free legal aid is available on 15100.",
  ],
  coverageNote:
    "It reads Indian statutes and regulations, plus judgments of the Supreme Court, the Punjab & Haryana High Court and the Delhi High Court. It does not cover district courts or consumer forums, and it can be out of date, so check the source before you rely on it.",
  examplesHeading: "Not sure how to put it? Try one of these",
  exampleQuestions: [
    "How much notice must my landlord give me before eviction?",
    "My employer has not paid my wages for three months. What are my rights?",
    "What must the police do when they arrest someone?",
  ],
  answerHeading: "What the law says",
  answerFramingNote:
    "This is assembled only from the sources listed below. Anything the law does not say, this will not say.",
  sourceKindStatute: "Act or regulation",
  sourceKindJudgment: "Court judgment",
  sourceKindDocument: "Government document",
  sourceOpensAt: (host) => `Opens ${host}`,
  notLegalAdvice: "This is legal information, not legal advice.",
};

const HI: UIStrings = {
  tagline: "भारतीय कानून पर मुफ़्त उत्तर, साथ में सरकारी स्रोत।",
  intro:
    "भारतीय कानून से जुड़ा सवाल पूछें और सरकारी स्रोत पर आधारित उत्तर पाएं, जिसे आप खुद जाँच सकते हैं। यह कानूनी जानकारी है, कानूनी सलाह नहीं। किसी खाते की ज़रूरत नहीं।",
  languageLabel: "भाषा / Language",
  questionLabel: "आपका सवाल",
  questionPlaceholder: "जैसे: बेदखली से पहले मकान मालिक को कितना नोटिस देना ज़रूरी है?",
  askButton: "पूछें",
  askingButton: "पूछा जा रहा है…",
  thinkingStatus: "कानून में खोजा जा रहा है। इसमें आधा मिनट तक लग सकता है।",
  genericError: "कुछ गड़बड़ हो गई। कृपया दोबारा प्रयास करें।",
  timeoutError: "अनुरोध में बहुत समय लग गया। कृपया अपना कनेक्शन जाँचें और दोबारा प्रयास करें।",
  sourcesHeading: "यह जानकारी कहाँ से है",
  corpusNote: (corpusVersion) =>
    `यह भारतीय कानून के ${corpusVersion} स्नैपशॉट पर आधारित है। इस पर भरोसा करने से पहले हमेशा आधिकारिक स्रोत जाँचें।`,
  unanswerableSuffix: "आप फिर भी एक निःशुल्क कानूनी सहायता वकील से मदद पा सकते हैं।",
  quotaRemainingLabel: "शेष निःशुल्क सवाल",
  resetsOnLabel: "अगली बार कब मिलेगा",
  signupCta: "अधिक सवालों के लिए मुफ़्त खाता बनाएं",
  noScriptNotice:
    "यह JavaScript सक्षम होने पर सबसे अच्छा काम करता है। इसके बिना, पेज फिर से लोड होने पर भी आपका सवाल सुरक्षित रहेगा, लेकिन आपको फिर से 'पूछें' दबाना होगा।",
  previousAnswerNotice: "नया उत्तर नहीं मिल सका। आपका पिछला उत्तर अभी भी नीचे दिखाया गया है।",
  helplineHeading: "अभी मदद चाहिए?",
  helplineNote: "भारत में किसी भी फ़ोन से, दिन हो या रात, कॉल निःशुल्क है।",
  crisisHeading: "मदद अभी उपलब्ध है",
  welcomeHeading: "यह क्या है",
  welcomePoints: [
    "इस्तेमाल पूरी तरह मुफ़्त — न खाता चाहिए, न लॉगिन।",
    "हर उत्तर सिर्फ़ उसी कानून से बनता है जिसका हवाला दिया जा सके, और हर स्रोत एक लिंक है जिसे आप खुद खोल सकते हैं।",
    "यह वकील नहीं है और कानूनी सलाह नहीं दे सकता। उसके लिए 15100 पर निःशुल्क कानूनी सहायता उपलब्ध है।",
  ],
  coverageNote:
    "इसमें भारतीय अधिनियम और नियम शामिल हैं, साथ ही उच्चतम न्यायालय, पंजाब एवं हरियाणा उच्च न्यायालय और दिल्ली उच्च न्यायालय के फैसले। ज़िला अदालतें और उपभोक्ता फोरम इसमें नहीं हैं, और जानकारी पुरानी भी हो सकती है — इसलिए भरोसा करने से पहले स्रोत ज़रूर देखें।",
  examplesHeading: "समझ नहीं आ रहा कैसे लिखें? इनमें से कोई चुनें",
  exampleQuestions: [
    "बेदखली से पहले मकान मालिक को मुझे कितना नोटिस देना होता है?",
    "मालिक ने तीन महीने से मेरी मज़दूरी नहीं दी। मेरे क्या अधिकार हैं?",
    "गिरफ़्तारी करते समय पुलिस को क्या-क्या करना ज़रूरी है?",
  ],
  answerHeading: "कानून क्या कहता है",
  answerFramingNote:
    "यह सिर्फ़ नीचे दिए गए स्रोतों से बना है। जो कानून में नहीं लिखा, वह यहाँ भी नहीं लिखा जाएगा।",
  sourceKindStatute: "अधिनियम या नियम",
  sourceKindJudgment: "अदालत का फैसला",
  sourceKindDocument: "सरकारी दस्तावेज़",
  sourceOpensAt: (host) => `${host} खुलेगा`,
  notLegalAdvice: "यह कानूनी जानकारी है, कानूनी सलाह नहीं।",
};

export function getStrings(language: Language["code"] | string): UIStrings {
  return language === "hi" ? HI : EN;
}
