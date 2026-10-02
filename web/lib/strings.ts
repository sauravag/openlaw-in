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
  /**
   * Names an untitled judgment by the case id the response carries. Judgment
   * payloads have no `title`, so without this the card could only show the
   * kind. It prints the corpus's own id verbatim -- it never composes a case
   * name or a court, which the response does not support.
   */
  sourceCaseLabel: (caseId: string) => string;
  /** Secondary line under a citation, naming the date it was decided. */
  sourceDecidedOn: (date: string) => string;
  /** Secondary line under a citation, naming the host it opens. */
  sourceOpensAt: (host: string) => string;
  notLegalAdvice: string;

  // --- Conversations and documents (Plan 6 Task 17) ---------------------
  /** Heading above the thread of questions and answers. */
  threadHeading: string;
  /** Small label above the person's own question inside a turn. */
  yourQuestionLabel: string;
  /** Names the documents that were read for one particular turn. */
  attachedToThisTurnLabel: string;
  /** Says a replayed turn came back from storage, and what is missing from it. */
  restoredTurnNote: string;
  /** The destructive control that removes a conversation and its documents. */
  deleteConversationLabel: string;
  /** Heading of the attached-documents section. */
  documentsHeading: string;
  /**
   * The honest paragraph: read only to answer, stored encrypted, deletable,
   * and (for an anonymous visitor) temporary. Never buried, never overstated.
   */
  documentsIntro: string;
  /** Accessible label of the file input itself. */
  attachFileLabel: string;
  /** Visible text of the upload form's submit button (the no-JavaScript path). */
  attachButton: string;
  /** States the limits BEFORE a file is chosen, so a refusal is never a surprise. */
  uploadLimitsNote: (maxMegabytes: number, maxDocuments: number) => string;
  /** Shown instead of the file input once the conversation holds its maximum. */
  uploadFullNote: (maxDocuments: number) => string;
  /**
   * The anonymous-retention sentence, with the one action that changes it.
   * `hours` comes from `documents.ANONYMOUS_UPLOAD_TTL_HOURS`, mirroring
   * `Settings.anonymous_upload_ttl_hours`.
   */
  anonymousUploadNotice: (hours: number) => string;
  signInCta: string;
  /** Progress line while a file is in flight. */
  uploadingStatus: (filename: string) => string;
  /** Accessible name of a document's delete button -- names the file. */
  deleteDocumentLabel: (filename: string) => string;
  /** Visible text of that same button, where the filename is already beside it. */
  deleteDocumentButton: string;
  documentPageCount: (count: number) => string;
  attachedToNextTurnLabel: string;
  /** Heads the region that holds the reader's OWN document content. */
  documentCitationsHeading: string;
  /** The sentence that makes the distinction impossible to miss. */
  documentNotLawNote: string;
  documentPageLabel: (page: number) => string;
  /** Upload refusals the client itself detects, before any byte is sent. */
  uploadTooLargeError: (maxMegabytes: number) => string;
  uploadUnsupportedTypeError: string;
  uploadTooManyDocumentsError: (maxDocuments: number) => string;
  uploadNoFileError: string;
  /** Escalation footer attached to a locally detected upload refusal. */
  uploadFailedFooter: string;
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
  sourceCaseLabel: (caseId) => `Case ${caseId}`,
  sourceDecidedOn: (date) => `Decided ${date}`,
  sourceOpensAt: (host) => `Opens ${host}`,
  notLegalAdvice: "This is legal information, not legal advice.",

  threadHeading: "Your conversation",
  yourQuestionLabel: "You asked",
  attachedToThisTurnLabel: "Read for this question",
  restoredTurnNote:
    "Restored after the page reloaded. The sources were not saved with it \u2014 ask again to see them.",
  deleteConversationLabel: "Delete this conversation and the documents in it",
  documentsHeading: "Your documents",
  documentsIntro:
    "A document you attach is read only to help answer your question. It is stored encrypted, it is not shown to anyone else, and you can delete it here at any time.",
  attachFileLabel: "Choose a document (PDF, Word or text)",
  attachButton: "Attach document",
  uploadLimitsNote: (maxMegabytes, maxDocuments) =>
    `Up to ${maxDocuments} documents, each no larger than ${maxMegabytes} MB. PDF, Word (.docx) and plain text only \u2014 photographs and scans cannot be read.`,
  uploadFullNote: (maxDocuments) =>
    `You have attached the most documents we can read at once (${maxDocuments}). Delete one to attach another.`,
  anonymousUploadNotice: (hours) =>
    `This document will be deleted after ${hours} hours. Sign in to keep it.`,
  signInCta: "Sign in",
  uploadingStatus: (filename) => `Sending ${filename}\u2026`,
  deleteDocumentLabel: (filename) => `Delete ${filename}`,
  deleteDocumentButton: "Delete",
  documentPageCount: (count) => (count === 1 ? "1 page" : `${count} pages`),
  attachedToNextTurnLabel: "These will be read for your next question.",
  documentCitationsHeading: "From your document",
  documentNotLawNote: "This is your own document, not law.",
  documentPageLabel: (page) => `Page ${page}`,
  uploadTooLargeError: (maxMegabytes) =>
    `That file is larger than ${maxMegabytes} MB, so it was not sent. Try a smaller file, or only the pages that matter.`,
  uploadUnsupportedTypeError:
    "We can only read PDF, Word (.docx) and plain text files. Photographs and scanned images cannot be read.",
  uploadTooManyDocumentsError: (maxDocuments) =>
    `You can attach at most ${maxDocuments} documents to one conversation. Delete one before adding another.`,
  uploadNoFileError: "No file was chosen, so nothing was sent.",
  uploadFailedFooter:
    "Your document was not stored. This is legal information, not legal advice \u2014 free legal aid is available on 15100.",
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
  sourceCaseLabel: (caseId) => `मामला ${caseId}`,
  sourceDecidedOn: (date) => `निर्णय की तारीख़ ${date}`,
  sourceOpensAt: (host) => `${host} खुलेगा`,
  notLegalAdvice: "यह कानूनी जानकारी है, कानूनी सलाह नहीं।",

  threadHeading: "आपकी बातचीत",
  yourQuestionLabel: "आपने पूछा",
  attachedToThisTurnLabel: "इस सवाल के लिए पढ़ा गया",
  restoredTurnNote:
    "पेज दोबारा लोड होने के बाद वापस लाया गया। इसके साथ स्रोत सुरक्षित नहीं रहते \u2014 उन्हें देखने के लिए सवाल फिर पूछें।",
  deleteConversationLabel: "यह बातचीत और इसमें रखे दस्तावेज़ मिटाएँ",
  documentsHeading: "आपके दस्तावेज़",
  documentsIntro:
    "आप जो दस्तावेज़ जोड़ते हैं, उसे सिर्फ़ आपके सवाल का जवाब देने में मदद के लिए पढ़ा जाता है। वह एन्क्रिप्टेड रूप में रखा जाता है, किसी और को नहीं दिखाया जाता, और आप उसे यहाँ से कभी भी मिटा सकते हैं।",
  attachFileLabel: "दस्तावेज़ चुनें (PDF, Word या टेक्स्ट)",
  attachButton: "दस्तावेज़ जोड़ें",
  uploadLimitsNote: (maxMegabytes, maxDocuments) =>
    `कुल ${maxDocuments} दस्तावेज़ तक, हर एक ${maxMegabytes} MB से बड़ा नहीं। सिर्फ़ PDF, Word (.docx) और सादा टेक्स्ट \u2014 फ़ोटो और स्कैन नहीं पढ़े जा सकते।`,
  uploadFullNote: (maxDocuments) =>
    `आपने एक साथ पढ़े जा सकने वाले सबसे ज़्यादा दस्तावेज़ (${maxDocuments}) जोड़ दिए हैं। नया जोड़ने के लिए एक मिटाएँ।`,
  anonymousUploadNotice: (hours) =>
    `यह दस्तावेज़ ${hours} घंटों के बाद मिटा दिया जाएगा। इसे रखना है तो साइन इन करें।`,
  signInCta: "साइन इन करें",
  uploadingStatus: (filename) => `${filename} भेजा जा रहा है\u2026`,
  deleteDocumentLabel: (filename) => `${filename} मिटाएँ`,
  deleteDocumentButton: "मिटाएँ",
  documentPageCount: (count) => `${count} पृष्ठ`,
  attachedToNextTurnLabel: "आपके अगले सवाल के लिए इन्हें पढ़ा जाएगा।",
  documentCitationsHeading: "आपके दस्तावेज़ से",
  documentNotLawNote: "यह आपका अपना दस्तावेज़ है, कानून नहीं।",
  documentPageLabel: (page) => `पृष्ठ ${page}`,
  uploadTooLargeError: (maxMegabytes) =>
    `यह फ़ाइल ${maxMegabytes} MB से बड़ी है, इसलिए भेजी नहीं गई। कोई छोटी फ़ाइल आज़माएँ, या सिर्फ़ ज़रूरी पन्ने भेजें।`,
  uploadUnsupportedTypeError:
    "हम सिर्फ़ PDF, Word (.docx) और सादा टेक्स्ट फ़ाइलें पढ़ सकते हैं। फ़ोटो और स्कैन की गई तस्वीरें नहीं पढ़ी जा सकतीं।",
  uploadTooManyDocumentsError: (maxDocuments) =>
    `एक बातचीत में ज़्यादा से ज़्यादा ${maxDocuments} दस्तावेज़ जोड़े जा सकते हैं। नया जोड़ने से पहले एक मिटाएँ।`,
  uploadNoFileError: "कोई फ़ाइल नहीं चुनी गई, इसलिए कुछ नहीं भेजा गया।",
  uploadFailedFooter:
    "आपका दस्तावेज़ सुरक्षित नहीं रखा गया। यह कानूनी जानकारी है, कानूनी सलाह नहीं \u2014 मुफ़्त कानूनी सहायता 15100 पर उपलब्ध है।",
};

export function getStrings(language: Language["code"] | string): UIStrings {
  return language === "hi" ? HI : EN;
}
