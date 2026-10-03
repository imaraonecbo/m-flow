import { fetchJson, cleanText } from '../net.js';

const URL = 'https://api.ted.europa.eu/v3/notices/search';

function firstText(value) {
  if (!value) return '';

  if (typeof value === 'string') {
    return cleanText(value);
  }

  if (Array.isArray(value)) {
    return firstText(value[0]);
  }

  if (typeof value === 'object') {
    return firstText(
      value.eng ??
      value.en ??
      Object.values(value)[0]
    );
  }

  return String(value);
}

function firstValue(value) {
  if (value == null) return '';

  if (Array.isArray(value)) {
    return firstValue(value[0]);
  }

  if (typeof value === 'object') {
    return firstValue(
      value.value ??
      value.eng ??
      value.en ??
      Object.values(value)[0]
    );
  }

  return String(value);
}

export class TedSource {
  name = 'ted';

  status() {
    const configured =
      Boolean(process.env.M_FLOW_TED_QUERY?.trim());

    return {
      enabled: configured,
      requiresCredential: false,
      ready: configured
    };
  }

  async discover({
    query =
      process.env.M_FLOW_TED_QUERY ||
      'FT~("information technology") SORT BY publication-date DESC',
    limit = 25
  } = {}) {

    if (!query?.trim()) {
      throw new Error('MISSING_ENV:M_FLOW_TED_QUERY');
    }

    const body = {
      query: query.trim(),

      /*
       * These are current TED Search API/eForms field names.
       * Avoid legacy/non-existent fields that can make the API reject
       * the entire request.
       */
      fields: [
        'publication-number',
        'publication-date',
        'notice-type',
        'notice-title',
        'buyer-name',
        'buyer-country',
        'classification-cpv',
        'total-value',
        'total-value-cur',
        'deadline',
        'links'
      ],

      page: 1,
      limit: Math.min(
        Math.max(Number(limit) || 25, 1),
        250
      ),

      scope: 'ACTIVE',
      checkQuerySyntax: false,
      paginationMode: 'PAGE_NUMBER'
    };

    const data = await fetchJson(
      URL,
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          accept: 'application/json'
        },
        body: JSON.stringify(body),
        timeoutMs: 30000
      }
    );

    const notices =
      Array.isArray(data.notices)
        ? data.notices
        : [];

    return notices
      .map((notice) => ({
        source: this.name,

        sourceId: String(
          notice['publication-number'] ??
          notice.publicationNumber ??
          ''
        ),

        title: firstText(
          notice['notice-title'] ??
          notice.title
        ),

        buyerName: firstText(
          notice['buyer-name'] ??
          notice.buyerName
        ),

        buyerCountry: firstText(
          notice['buyer-country'] ??
          notice.buyerCountry
        ),

        sector:
          Array.isArray(
            notice['classification-cpv']
          )
            ? notice['classification-cpv']
                .map(String)
                .filter(Boolean)
            : firstValue(
                notice['classification-cpv']
              )
                ? [
                    firstValue(
                      notice['classification-cpv']
                    )
                  ]
                : [],

        estimatedValue: firstValue(
          notice['total-value'] ??
          notice['estimated-value-proc']
        ),

        currency: firstValue(
          notice['total-value-cur'] ??
          notice['estimated-value-cur-proc']
        ),

        deadline: firstText(
          notice.deadline ??
          notice['deadline-receipt-tender-date'] ??
          notice[
            'deadline-receipt-tender-date-lot'
          ]
        ),

        url:
          notice.links?.[0]?.href ??
          notice.links?.html ??
          notice['publication-number']
            ? (
                notice.links?.[0]?.href ??
                `https://ted.europa.eu/en/notice/-/detail/${notice['publication-number']}`
              )
            : null,

        raw: notice
      }))
      .filter(
        (item) =>
          item.sourceId &&
          item.title
      );
  }
}
