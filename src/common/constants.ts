const siteUrlStem = 'https://univac-1.github.io/ai-info-rss-feed';
const siteUrl = `${siteUrlStem}/`;

export default {
  // サイト設定
  siteUrl: `${siteUrl}`,
  siteUrlStem: siteUrlStem,
  siteTitle: 'AI関連情報RSS',
  siteDescription:
    'AI関連情報の更新をまとめたRSSフィードを配信しています。記事を読んでAI技術の最新動向や質の高い技術情報を得られることを目的としています。',

  // フィード設定
  feedTitle: 'AI関連情報RSS',
  feedDescription: 'AI関連情報の更新をまとめたRSSフィード',
  feedLanguage: 'ja',
  feedCopyright: 'univac-1/ai-info-rss-feed',
  feedGenerator: 'univac-1/ai-info-rss-feed',
  feedUrls: {
    atom: `${siteUrl}feeds/atom.xml`,
    rss: `${siteUrl}feeds/rss.xml`,
    json: `${siteUrl}feeds/feed.json`,
  },

  // リンク
  author: 'univac-1',
  gitHubUserUrl: 'https://github.com/univac-1/',
  gitHubRepositoryUrl: 'https://github.com/univac-1/ai-info-rss-feed/',
  xUserUrl: 'https://x.com/univac-1',

  // Google Analytics系。フォークして使う際は値を空にするか書き換えてください
  googleSiteVerification: 'GPLvXv8kYtLMW912ZS54DKFEZL6ruOrjOFLdHVTo37o',
  globalSiteTagKey: 'G-CNNNTL0NB3',

  // フィードの取得などに使う UserAgent
  requestUserAgent: 'facebookexternalhit/1.1; univac-1/ai-info-rss-feed',

  // サイトの追加方法のリンク
  howToAddSiteLink:
    'https://github.com/univac-1/ai-info-rss-feed#%E3%82%B5%E3%82%A4%E3%83%88%E3%81%AE%E8%BF%BD%E5%8A%A0%E6%96%B9%E6%B3%95',

  // 処理の設定
  feedFetchConcurrency: 50, // フィードを取得する並列数
  feedOgFetchConcurrency: 20, // OG情報を取得する並列数
  aggregateFeedDurationInHours: 8 * 24, // まとめフィードの対象となる時間の範囲
  maxFeedDescriptionLength: 200, // フィードのdescriptionの最大文字数
  maxFeedContentLength: 500, // フィードのcontentの最大文字数
  processImageConcurrency: 50, // 画像の処理の並列数。画像取得と変換
  eleventyFetchConcurrency: 50, // Eleventyの画像取得の並列数
  fetchedFeedCacheDurationInHours: 1, // フィードのキャッシュの有効時間
  fetchedOgCacheDurationInHours: 24, // OG情報のキャッシュの有効時間
};
