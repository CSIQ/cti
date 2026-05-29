import nvdlib
import datetime
import json
import os
import re
from .utils import send_to_logic_app

keyword_path = 'keyword_list.txt'

def load_keywords(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()
    
    keyword_map = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if ':' in line:
            parts = line.split(':', 1)
            kw = parts[0].strip()
            try:
                threshold = float(parts[1].strip())
                keyword_map[kw] = threshold
            except ValueError:
                # If not a valid float, treat the whole line as a keyword with default threshold
                keyword_map[line] = 9.0
        else:
            keyword_map[line] = 9.0
    return keyword_map

keyword_map = load_keywords(keyword_path)
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
IS_DRY_RUN = os.environ.get("IS_DRY_RUN", "false").lower() == "true"
NVD_API_KEY = os.environ.get("NVD_API_KEY")
EXCLUDED_VULN_STATUSES = {
    "DEFERRED",
    "AWAITING ANALYSIS",
    "UNDERGOING ANALYSIS",
}


def get_score_and_severity(cve_item):
    final_score = 0
    final_severity = "Unknown"
    if hasattr(cve_item.metrics, 'cvssMetricV31'):
        metric_list = cve_item.metrics.cvssMetricV31
    elif hasattr(cve_item.metrics, 'cvssMetricV30'):
        metric_list = cve_item.metrics.cvssMetricV30
    else:
        return final_score, final_severity
    
    for metric in metric_list:
        try:
            current_score = metric.cvssData.baseScore
            current_severity = metric.cvssData.baseSeverity
            current_source = metric.source
            if current_source == "nvd@nist.gov":
                return current_score, current_severity
            else:
                final_score = current_score
                final_severity = current_severity
                # return None, None
        except Exception as e:
            print(f"Error processing metric: {e}")
            continue
    return final_score, final_severity

def should_exclude_by_status(cve_item):
    status = str(getattr(cve_item, "vulnStatus", "")).strip().upper()
    return status in EXCLUDED_VULN_STATUSES

def search_critical_cve_data(
    start_date_for_query, end_date_for_query, NVD_API_KEY
):
    """
    搜尋 CVE 並將結果格式化為適合寫入 CSV 的列表。
    返回一個列表，其中每個元素都是 CSV 的一列。
    """
    print("Searching for CVEs...")
    cve_items = nvdlib.searchCVE(
        lastModStartDate=start_date_for_query,
        lastModEndDate=end_date_for_query,
        key=NVD_API_KEY
    )
    print(f"Found {len(cve_items)} CVEs to process.")
    critical_cve = []
    min_threshold = min(keyword_map.values()) if keyword_map else 9.0
    
    for cve in cve_items:
        if should_exclude_by_status(cve):
            # print(f"Skip {cve.id} due to vulnStatus={getattr(cve, 'vulnStatus', 'Unknown')}")
            continue
        score, severity = get_score_and_severity(cve)
        if score >= min_threshold and cve.descriptions[0].value:
            description = cve.descriptions[0].value.replace("\n", " ").replace("\r", " ")
            match_keywords = []
            for k, threshold in keyword_map.items():
                if score < threshold:
                    continue
                k_lower = k.lower()
                description_lower = description.lower()
                prefix = r"\b" if k_lower[0].isalnum() else r""
                suffix = r"\b" if k_lower[-1].isalnum() else r""
                pattern = prefix + re.escape(k_lower) + suffix
                if re.search(pattern, description_lower):
                    match_keywords.append(k)
            if not match_keywords:
                continue
            print(f"  - {cve.id} | Score: {score} | Severity: {severity} | Keywords: {', '.join(match_keywords)}")
            date_str = start_date_for_query.strftime('%Y-%m-%d')

            row_data = {
                "Date": date_str,
                "CVE": cve.id,
                "CVSS score": str(score),
                "Keywords": ", ".join(match_keywords),
                "Description": description
            }
            critical_cve.append(row_data)
    print(f"Found {len(critical_cve)} CVEs matching keywords.")
    return critical_cve

def main(event, context):
    start_date_for_query = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    end_date_for_query = datetime.datetime.now(datetime.timezone.utc)
    print(f"date:{start_date_for_query}")
    critical_cve = search_critical_cve_data(
        start_date_for_query, end_date_for_query, NVD_API_KEY
    )
    critical_cve.sort(key=lambda x: x["Date"])
    
    if IS_DRY_RUN:
        return
    if critical_cve:
        payload = {
            "Source": "NVD",
            "Details": critical_cve
        }
        send_to_logic_app(payload)
    else:
        # 如果沒有找到任何漏洞，也發送一個通知
        print("No new vulnerabilities found to report.")

    return {"statusCode": 200, "body": json.dumps("CVE scan process completed.")}

if __name__ == "__main__":
    main(None, None)