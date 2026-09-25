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
 */
export interface UIStrings {
  intro: string;
  questionLabel: string;
  questionPlaceholder: string;
  askButton: string;
  askingButton: string;
  genericError: string;
  timeoutError: string;
  malformedResponseError: string;
  sourcesHeading: string;
  corpusNote: (corpusVersion: string) => string;
  unanswerableSuffix: string;
  quotaRemainingLabel: string;
  resetsOnLabel: string;
  signupCta: string;
  noScriptNotice: string;
  previousAnswerNotice: string;
}

const EN: UIStrings = {
  intro:
    "Ask a question about Indian law and get an answer grounded in a government source you can check yourself. This is legal information, not legal advice. No account needed.",
  questionLabel: "Your question",
  questionPlaceholder: "e.g. How much notice must my landlord give before eviction?",
  askButton: "Ask",
  askingButton: "Asking…",
  genericError: "Something went wrong. Please try again.",
  timeoutError: "The request took too long. Please check your connection and try again.",
  malformedResponseError: "Received an unexpected response from the server. Please try again.",
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
};

const HI: UIStrings = {
  intro:
    "भारतीय कानून से जुड़ा सवाल पूछें और सरकारी स्रोत पर आधारित उत्तर पाएं, जिसे आप खुद जाँच सकते हैं। यह कानूनी जानकारी है, कानूनी सलाह नहीं। किसी खाते की ज़रूरत नहीं।",
  questionLabel: "आपका सवाल",
  questionPlaceholder: "जैसे: बेदखली से पहले मकान मालिक को कितना नोटिस देना ज़रूरी है?",
  askButton: "पूछें",
  askingButton: "पूछा जा रहा है…",
  genericError: "कुछ गड़बड़ हो गई। कृपया दोबारा प्रयास करें।",
  timeoutError: "अनुरोध में बहुत समय लग गया। कृपया अपना कनेक्शन जाँचें और दोबारा प्रयास करें।",
  malformedResponseError: "सर्वर से अप्रत्याशित उत्तर मिला। कृपया दोबारा प्रयास करें।",
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
};

export function getStrings(language: Language["code"] | string): UIStrings {
  return language === "hi" ? HI : EN;
}
