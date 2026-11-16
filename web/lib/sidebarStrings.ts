import type { Language } from './languages'

/**
 * Copy for the conversation sidebar, its phone panel and the "Chats" button
 * (signed-in users, Mode B only). Kept out of `lib/strings.ts`, which every
 * page imports, so none of it ships in the Mode A bundle: import this only
 * from code that is itself rendered behind `AUTH_ENABLED`
 * (`npm run check:mode-a-bundle` has a sentinel for it).
 */
export interface SidebarStrings {
  conversationsNavLabel: string
  newChatButton: string
  showTitlesLabel: string
  documentsOnlyTitle: string
  showMoreConversations: string
  conversationsMenuButton: string
  closeConversationsPanel: string
  todayAt: (time: string) => string
  yesterdayAt: (time: string) => string
  /** "" for no documents, so the entry shows only the time. */
  conversationDocuments: (count: number) => string
}

const EN: SidebarStrings = {
  conversationsNavLabel: 'Your conversations',
  newChatButton: 'New chat',
  showTitlesLabel: 'Show titles',
  documentsOnlyTitle: 'Documents only',
  showMoreConversations: 'Show more',
  conversationsMenuButton: 'Chats',
  closeConversationsPanel: 'Close',
  todayAt: (time) => `Today, ${time}`,
  yesterdayAt: (time) => `Yesterday, ${time}`,
  conversationDocuments: (count) =>
    count === 0 ? '' : count === 1 ? '1 document' : `${count} documents`,
}

const HI: SidebarStrings = {
  conversationsNavLabel: 'आपकी बातचीत',
  newChatButton: 'नई बातचीत',
  showTitlesLabel: 'शीर्षक दिखाएं',
  documentsOnlyTitle: 'केवल दस्तावेज़',
  showMoreConversations: 'और दिखाएं',
  conversationsMenuButton: 'बातचीत',
  closeConversationsPanel: 'बंद करें',
  todayAt: (time) => `आज, ${time}`,
  yesterdayAt: (time) => `कल, ${time}`,
  conversationDocuments: (count) => (count === 0 ? '' : `${count} दस्तावेज़`),
}

export function getSidebarStrings(language: Language['code'] | string): SidebarStrings {
  return language === 'hi' ? HI : EN
}
