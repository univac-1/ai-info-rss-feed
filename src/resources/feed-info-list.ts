type ValidUrl = `${'http' | 'https'}://${string}.${string}`;

type FeedInfoTuple = [label: string, url: ValidUrl];

export interface FeedInfo {
  label: string;
  url: ValidUrl;
}

const createFeedInfoList = (feedInfoTuples: FeedInfoTuple[]) => {
  const feedInfoList: FeedInfo[] = [];

  for (const [label, url] of feedInfoTuples) {
    feedInfoList.push({
      label,
      url,
    });
  }

  return feedInfoList;
};

/**
 * フィード情報一覧。アルファベット順
 * ラベルが被るとバリデーションエラーになるので別のラベルを設定してください
 */
// prettier-ignore
export const FEED_INFO_LIST: FeedInfo[] = createFeedInfoList([
  // ['企業名・製品名など', 'RSS/AtomフィードのURL'],
  ['ITmedia AI+', 'https://rss.itmedia.co.jp/rss/2.0/aiplus.xml'],
  ['Zenn（機械学習タグ）', 'https://zenn.dev/topics/機械学習/feed'],
  ['Zenn（AIタグ）', 'https://zenn.dev/topics/ai/feed'],
  ['Zenn（生成AIタグ）', 'https://zenn.dev/topics/生成ai/feed'],
  ['Zenn（DLタグ）', 'https://zenn.dev/topics/deeplearning/feed'],
  ['Zenn（LLMタグ）', 'https://zenn.dev/topics/llm/feed'],
  ['Qiita（機械学習タグ）', 'https://qiita.com/tags/機械学習/feed'],
  ['Qiita（AIタグ）', 'https://qiita.com/tags/AI/feed'],
  ['Qiita（生成AIタグ）', 'https://qiita.com/tags/生成AI/feed'],
  ['Qiita（DLタグ）', 'https://qiita.com/tags/DL/feed'],
  ['Qiita（LLMタグ）', 'https://qiita.com/tags/LLM/feed'],
]);
