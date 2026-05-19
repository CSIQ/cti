import feedparser
import datetime
import requests
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any
import warnings
import json
import os
import base64
import markdown
from .utils import send_email, send_to_logic_app

warnings.filterwarnings("ignore")
IS_DRY_RUN = os.environ.get("IS_DRY_RUN", "false").lower() == "true"
GPT_KEY = base64.b64decode(os.getenv("GPT_KEY_B64")).decode('utf-8').strip()
OPENAI_URL = os.getenv("OPENAI_URL")

news_keyword_path = 'news_keyword.txt'
news_keywords = open(news_keyword_path, 'r', encoding='utf-8').read().splitlines()


def extract_summary_section(group_summary: str) -> str:
    """
    從完整的新聞摘要中提取「摘要」部分的文字。
    :param group_summary: 完整的新聞摘要內容
    :return: 僅包含摘要部分的文字
    """
    pattern = r'####\s*摘要[：:]\s*\n(.*?)(?=\n####|\Z)'
    match = re.search(pattern, group_summary, re.DOTALL | re.IGNORECASE)
    
    if match:
        summary_text = match.group(1).strip()
        return summary_text
    else:
        return group_summary.strip()

def match_keywords(text: str) -> List[str]:
    """
    檢查文字中是否包含關鍵字，返回匹配的關鍵字列表。
    """
    match_keywords = []
    text_lower = text.lower()
    
    for keyword in news_keywords:
        keyword_lower = keyword.lower()
        prefix = r"\b" if keyword_lower[0].isalnum() else r""
        suffix = r"\b" if keyword_lower[-1].isalnum() else r""
        pattern = prefix + re.escape(keyword_lower) + suffix
        
        if re.search(pattern, text_lower):
            match_keywords.append(keyword)
    
    return match_keywords

def fetch_article_content(url: str) -> str:
    """
    抓取指定 URL 的網頁主文。
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Referer": "https://www.google.com/",
        }
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        article_body = (
            soup.find("div", class_="articlebody")
            or soup.find("div", class_="content")
            or soup.find("div", class_="zox-post-body")
            or soup.find("article")
            or soup.find("section", class_="content")
        )

        if article_body:
            content = article_body.get_text(separator=" ", strip=True)
        else:
            content = soup.body.get_text(separator=" ", strip=True) if soup.body else ""

        return content

    except Exception as e:
        print(f"Error fetching content from {url}: {e}")
        return ""

def crawl_and_filter_yesterday_news(rss_feed_url, date_format):
    filtered_news = []
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    try:
        # Parse the RSS feed and handle potential exceptions
        feed = feedparser.parse(rss_feed_url)
        if rss_feed_url == "https://www.ithome.com.tw/rss/security":
            response = requests.get(rss_feed_url, verify=False)
            feed = feedparser.parse(response.content)
        for entry in feed.entries:
            publication_date = entry.published_parsed
            if publication_date:
                try:
                    entry_date = datetime.datetime.strptime(
                        entry.published, date_format
                    )
                except ValueError:
                    # Handle potential parsing error
                    print(f"Error parsing publication date for entry: {entry.title}")
                    continue

                if entry_date.date() == yesterday:
                    cleaned_title = re.sub(r"<[^>]*>", "", entry.title).strip()
                    cleaned_description = re.sub(
                        r"<[^>]*>", "", entry.description
                    ).strip()
                    full_content = fetch_article_content(entry.link)
                    content_to_use = full_content if full_content else cleaned_description
                    if re.search(r'CVE-\d{4}-\d{4,7}', cleaned_title, re.IGNORECASE) or re.search(r'CVE-\d{4}-\d{4,7}', content_to_use, re.IGNORECASE):
                        continue
                    title_keywords = match_keywords(cleaned_title)
                    content_keywords = match_keywords(content_to_use)
                    all_keywords = list(set(title_keywords + content_keywords))
                    if all_keywords:
                        print(f"Matched News: {cleaned_title} | Keywords: {', '.join(all_keywords)}")
                        news_item = {
                            "title": cleaned_title,
                            "description": content_to_use,
                            "link": entry.link,
                            "date": entry_date.strftime("%Y-%m-%d %H:%M:%S"),
                            "keywords": all_keywords
                        }
                        filtered_news.append(news_item)
    except Exception as e:
        print(f"Error parsing RSS feed: {e}")
    return filtered_news

def group_news_by_topic(news_items: List[Dict[str, str]]) -> List[List[int]]:
    """
    使用 AI 將新聞根據主題進行分組。
    :param news_items: 所有新聞項目的列表。
    :return: 一個包含分組後索引的列表，例如 [[0, 2], [1], [3, 4]]
    """
    if not news_items:
        return []

    print("Phase 1: Grouping news by topic...")

    titles_with_indices = "\n".join([f"{i+1}. {item['title']}" for i, item in enumerate(news_items)])

    prompt = f"""你是一位資安新聞分析師。你的任務是將以下新聞標題列表根據「相同事件」進行分組。

    規則：
    1. 只需回傳一個 JSON 物件，其中包含一個名為 "groups" 的鍵，其值為一個列表。列表中的每個子列表應包含屬於同一主題的新聞的「原始編號」。
    2. 「相同事件」指的是針對同一家公司、同一個漏洞 (CVE)、或同一個惡意軟體活動的直接報導。
    3. 綜合性報導（如「資安日報」）或一般性主題（如「勒索軟體趨勢」）應各自獨立為一個分組。
    4. 請務必包含所有新聞的編號，不可遺漏任何一則。

    範例輸入：
    1. CISA Warns of Actively Exploited Microsoft SharePoint Vulnerability
    2. New Ransomware Strain 'BlackCat' Emerges
    3. Microsoft Patches Critical SharePoint Flaw (CVE-2024-12345)
    4. Daily Security Briefing: Phishing and More
    5. Attackers Leveraging CVE-2024-12345 in SharePoint Servers

    範例輸出：
    {{
    "groups": [[1, 3, 5], [2], [4]]
    }}

    ---
    以下是待分組的新聞標題 (共 {len(news_items)} 則)：
    {titles_with_indices}
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": str(GPT_KEY),
        }
        data = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.1,
            "response_format": {"type": "json_object"} # 要求 AI 輸出 JSON
        }
        response = requests.post(OPENAI_URL, headers=headers, json=data, verify=False)

        if response.status_code == 200:
            result = response.json()
            usage_data = result.get("usage") or result.get("data", {}).get("usage")
            groups_json = json.loads(result["data"]["choices"][0]["message"]["content"])
            groups = [[idx - 1 for idx in group] for group in groups_json.get("groups", [])]
            print(f"Successfully grouped news into {len(groups)} topics.")
            return groups, usage_data
        else:
            print(f"API request for grouping failed: {response.status_code} - {response.text}")
            return [[i] for i in range(len(news_items))], None

    except Exception as e:
        print(f"Error during news grouping: {e}")
        return [[i] for i in range(len(news_items))], None

def summarize_news_group(news_group: List[Dict[str, str]], original_indices: List[int], total_count: int) -> str:
    """
    使用 OpenAI GPT API 整理單一「主題」的新聞。
    :param news_group: 同一主題的多篇新聞項目
    :param original_indices: 這組新聞在總列表中的原始編號 (1-based)
    :param total_count: 新聞總數
    :return: 該主題的 Markdown 格式摘要區塊
    """
    try:
        news_content = ""
        links = set()
        for item in news_group:
            news_content += f"- 標題: {item['title']}\n"
            news_content += f"  描述: {item['description']}\n"
            news_content += f"  連結: {item['link']}\n\n"
            links.add(item['link'])


        # 將原始編號格式化，例如 (1, 5 / 20)
        indices_str = ','.join(map(str, original_indices))
        links_str = "\n".join([f"- {link}" for link in sorted(list(links))])

        prompt = f"""你是一位頂尖的資安新聞分析師。你的任務是根據提供的新聞內容，嚴格按照下方的「輸出格式範本」來填寫一份繁體中文摘要。

        #### **規則與指令**
        1.  **絕對遵循範本**：你的輸出必須與下方的範本格式完全一致。不得添加、修改或刪除任何標題或格式。
        2.  **禁止使用 `•` 符號**：所有列表項都必須且只能使用減號 `-` 開頭，後面跟一個空格。
        3.  **無額外標題**：不要輸出 "## 整合式新聞摘要" 或任何更高層級的標題。
        4.  **連結處理**：直接使用已整理好的連結列表，不要自己添加或修改。

        ---
        #### **輸出格式範本 (請嚴格遵循此格式填寫)**

        ### [請在此為這個主題生成一個最能代表性的整合標題]
        #### 摘要：
        (在此撰寫一段約 100-150 字的綜合文字摘要，總結事件的核心內容、影響和結論。)

        #### 威脅類型：
        - (項目一)
        - (項目二)
        - (更多項目...)

        #### 風險評估：
        - **等級：** 高／中／低
        - **理由：** (在此簡述評估理由。如果來源資訊不足或意見不一，請整合說明。)

        #### 受影響範圍：
        - **廠商/組織：** (受影響的具體廠商或組織，若無則寫「不適用」)
        - **產品/系統：** (受影響的具體產品或系統，若無則寫「不適用」)
        - **版本/對象：** (受影響的具體版本或用戶群體，若無則寫「不適用」)

        #### 相關連結：
        {links_str}

        ---
        #### **以下為待分析的新聞內容 (共 {len(news_group)} 則)**
        {news_content}
        """
        # prompt = prompt.replace("https", "hxxps")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": str(GPT_KEY),
        }
        data = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.2,
        }

        response = requests.post(OPENAI_URL, headers=headers, json=data, verify=False)

        if response.status_code == 200:
            result = response.json()
            usage_data = result.get("usage") or result.get("data", {}).get("usage")
            content = result["data"]["choices"][0]["message"]["content"]
            return content, usage_data
        else:
            print(f"API request for group summary failed with status code: {response.status_code}")
            return f"#### (編號 {indices_str}) 摘要生成錯誤\n- 原因: API 請求失敗，狀態碼 {response.status_code}"

    except Exception as e:
        print(f"Error with GPT group summarization: {e}")
        return "",None

def generate_global_summary(full_summary_text: str) -> str:
    """
    基於所有已完成的摘要，生成最終的全域總結部分。
    :param full_summary_text: 已合併的所有主題摘要內容
    :return: 全域總結的 Markdown 文本
    """
    print("Phase 3: Generating global summary...")
    try:
        prompt = f"""你是一位頂尖的資安新聞分析師。請基於下方已整理好的新聞摘要，產出最後的「全域分類總結」部分。如果沒有新聞摘要，請輸出本日無新聞摘要。

        #### 規則
        - **僅需輸出 `### 全域分類總結` 這個章節的完整內容**，不要包含其他標題或文字。
        - 內容需簡潔、條列式，彙整所有事件。

        ---

        ### 全域分類總結

        #### 重要資安威脅或漏洞的簡要說明
        - (在此條列濃縮所有事件的核心威脅)

        #### 受影響的廠商,產品,版本或惡意軟體組織
        - (在此條列匯總所有提及的產業/產品/版本/威脅組織)

        ---

        ### 以下為已完成的新聞摘要內容
        {full_summary_text}
        """
        headers = {
            "Content-Type": "application/json",
            "x-api-key": str(GPT_KEY),
        }
        data = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.2,
        }
        response = requests.post(OPENAI_URL, headers=headers, json=data, verify=False)

        if response.status_code == 200:
            result = response.json()
            usage_data = result.get("usage") or result.get("data", {}).get("usage")
            print("Successfully generated global summary.")
            return result["data"]["choices"][0]["message"]["content"], usage_data
        else:
            print(f"API request for global summary failed: {response.status_code}")
            return "### 全域總結生成失敗", None
    except Exception as e:
        print(f"Error generating global summary: {e}")
        return f"### 全域總結生成異常: {e}", None
    
def main(event, context):
    token_usage_stats = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0
    }
    def update_token_usage(usage_data: Dict[str, int]):
        if usage_data and isinstance(usage_data, dict):
            token_usage_stats["prompt_tokens"] += usage_data.get("prompt_tokens", 0)
            token_usage_stats["completion_tokens"] += usage_data.get("completion_tokens", 0)
            token_usage_stats["total_tokens"] += usage_data.get("total_tokens", 0)
    rss_feeds = [
        {"url": "https://feeds.feedburner.com/TheHackersNews", "date_format": "%a, %d %b %Y %H:%M:%S %z"},
        {"url": "https://www.bleepingcomputer.com/feed/", "date_format": "%a, %d %b %Y %H:%M:%S %z"},
        {"url": "https://www.ithome.com.tw/rss/security", "date_format": "%Y-%m-%d %H:%M"},
        {"url": "https://feeds.feedburner.com/threatintelligence/pvexyqv7v0v", "date_format": "%a, %d %b %Y %H:%M:%S %z"},
        {"url": "https://api.msrc.microsoft.com/update-guide/RSS", "date_format": "%a, %d %b %Y %H:%M:%S %z"},
    ]
    recipients = ["Benson.Gong@quantatw.com",
                  "Thomas.Jheng@quantatw.com", 
                  "Tyler.Liao@quantatw.com",
                  "alfred.chen@quantatw.com",
                  "linda.hsu@quantatw.com",
                  "peggy_huang@quantatw.com",
                  "Vincent.Xu@quantatw.com",
                  "Sean-Cheng@quantatw.com",
                  "Dragon.Chen@quantatw.com"]

    all_filtered_news = []
    for rss_feed in rss_feeds:
        filtered_news = crawl_and_filter_yesterday_news(rss_feed["url"], rss_feed["date_format"])
        all_filtered_news.extend(filtered_news)

    total_news_count = len(all_filtered_news)

    if not all_filtered_news:
        print("There is no matched News today.")
        if not IS_DRY_RUN:
            payload = {
            "Recipients": recipients,
            "Summary": "## 今日資安新聞摘要\n\n> 本日無新聞摘要。"
            }
            send_email(payload)
        return {"statusCode": 200, "body": json.dumps("No news to process.")}

    # news_groups 是一個索引列表, e.g., [[0, 2], [1], [3, 4]]
    news_groups_indices, usage = group_news_by_topic(all_filtered_news)
    update_token_usage(usage)

    summarized_blocks = []
    news_for_logic_app = []

    for i, group_indices in enumerate(news_groups_indices):
        news_group_items = [all_filtered_news[i] for i in group_indices]
        original_numbers = [i + 1 for i in group_indices]

        print(f"Phase 2: Summarizing group {i+1}/{len(news_groups_indices)} (News #{original_numbers})...")
        print(f"News Group Items: {news_group_items}")
        group_summary, usage = summarize_news_group(news_group_items, original_numbers, total_news_count)
        summarized_blocks.append(group_summary)
        if usage:
            update_token_usage(usage)

        if news_group_items:
            first_item = news_group_items[0]
            news_data = {
                "Date": first_item["date"],
                "Link": first_item["link"],
                "Keywords": ", ".join(first_item['keywords']),
                "Description": extract_summary_section(group_summary)
            }
            news_for_logic_app.append(news_data)

    main_summary_content = "\n\n---\n\n".join(filter(None, summarized_blocks))

    global_summary_content, usage = generate_global_summary(main_summary_content)
    update_token_usage(usage)


    final_summary = f"## 今日資安新聞摘要 ({datetime.date.today().strftime('%Y-%m-%d')})\n\n"
    final_summary += f"> 本日共有 **{total_news_count}** 則新聞\n\n"
    final_summary += main_summary_content
    final_summary += "\n\n---\n\n"
    final_summary += global_summary_content

    print("\n--- FINAL SUMMARY ---\n")
    print(final_summary)

    print("\n--- TOKEN USAGE ---")
    print(f"Prompt Tokens: {token_usage_stats['prompt_tokens']}")
    print(f"Completion Tokens: {token_usage_stats['completion_tokens']}")
    print(f"Total Tokens Used: {token_usage_stats['total_tokens']}")

    if not IS_DRY_RUN:
        payload = { 
            "Recipients": recipients,
            "Summary": markdown.markdown(final_summary)
        }
        send_email(payload)
        if news_for_logic_app:
            logic_app_payload = {
                "Source": "News",
                "Details": news_for_logic_app
            }
            send_to_logic_app(logic_app_payload)
            print(f"Sent {len(news_for_logic_app)} news items to Logic App.")

    return {
        "statusCode": 200,
        "body": json.dumps("News processing completed successfully!"),
    }

if __name__ == "__main__":
    main(None, None)