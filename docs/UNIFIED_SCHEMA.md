# Unified Schema Reference ("Clean Union")

This document defines the standard top-level columns used across all Arctic Shift Parquet files. This "Clean Union" strategy ensures that all datasets share a consistent core schema while preserving 100% of the original data via the `extra_json` column.

## 1. Core Identity & Search Columns (Shared)
These columns are guaranteed to be at the top level in both **RC** and **RS** files and are used for primary search and indexing.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `VARCHAR` | The unique ID of the post or comment. |
| `author` | `VARCHAR` | The username of the creator. |
| `subreddit` | `VARCHAR` | The subreddit name. |
| `created_utc` | `VARCHAR` / `BIGINT` | Epoch timestamp of creation. |
| `score` | `BIGINT` | The net upvotes/downvotes. |
| `edited` | `BIGINT` | Normalized timestamp or flag (0/1). |

## 2. Reddit Comments (RC) Schema
Standard top-level columns for comment archives.

**Key Fields**:
`body`, `link_id`, `parent_id`, `controversiality`, `gilded`, `distinguished`, `replies` (if simple), `author_flair_text`, `author_flair_css_class`.

**Full Standard Set (Usage > 20%)**:
`_meta`, `all_awardings`, `approved`, `approved_at_utc`, `approved_by`, `archived`, `associated_award`, `author`, `author_cakeday`, `author_created_utc`, `author_flair_background_color`, `author_flair_css_class`, `author_flair_richtext`, `author_flair_template_id`, `author_flair_text`, `author_flair_text_color`, `author_flair_type`, `author_fullname`, `author_is_blocked`, `author_patreon_flair`, `author_premium`, `awarders`, `banned_at_utc`, `banned_by`, `body`, `body_html`, `can_gild`, `can_mod_post`, `collapsed`, `collapsed_because_crowd_control`, `collapsed_reason`, `collapsed_reason_code`, `comment_type`, `controversiality`, `created`, `created_utc`, `distinguished`, `downs`, `editable`, `edited`, `expression_asset_data`, `gilded`, `gildings`, `id`, `ignore_reports`, `is_submitter`, `likes`, `link_id`, `locked`, `media_metadata`, `mod_note`, `mod_reason_by`, `mod_reason_title`, `mod_reports`, `name`, `no_follow`, `num_reports`, `parent_id`, `permalink`, `permalink_url`, `profile_img`, `profile_over_18`, `quarantined`, `removal_reason`, `removed`, `replies`, `report_reasons`, `retrieved_on`, `retrieved_utc`, `rte_mode`, `saved`, `score`, `score_hidden`, `send_replies`, `spam`, `steward_reports`, `stickied`, `subreddit`, `subreddit_id`, `subreddit_name_prefixed`, `subreddit_type`, `top_awarded_type`, `total_awards_received`, `treatment_tags`, `unrepliable_reason`, `ups`, `user_reports`.

## 3. Reddit Submissions (RS) Schema
Standard top-level columns for post archives.

**Key Fields**:
`title`, `selftext`, `url`, `domain`, `num_comments`, `over_18`, `is_self`, `is_video`, `thumbnail`, `link_flair_text`.

**Full Standard Set (Usage > 20%)**:
`_meta`, `ad_business`, `ad_promoted_user_posts`, `ad_supplementary_text_md`, `ad_user_targeting`, `adserver_click_url`, `adserver_imp_pixel`, `all_awardings`, `allow_live_comments`, `app_store_data`, `approved`, `approved_at_utc`, `approved_by`, `archived`, `author`, `author_cakeday`, `author_created_utc`, `author_flair_background_color`, `author_flair_css_class`, `author_flair_richtext`, `author_flair_template_id`, `author_flair_text`, `author_flair_text_color`, `author_flair_type`, `author_fullname`, `author_id`, `author_is_blocked`, `author_patreon_flair`, `author_premium`, `awarders`, `ban_note`, `banned_at_utc`, `banned_by`, `brand_safe`, `call_to_action`, `campaign_id`, `can_gild`, `can_mod_post`, `category`, `clicked`, `collections`, `content_categories`, `contest_mode`, `created`, `created_utc`, `crosspost_parent`, `crosspost_parent_list`, `disable_comments`, `discussion_type`, `distinguished`, `domain`, `domain_override`, `downs`, `edited`, `embed_type`, `embed_url`, `event_end`, `event_is_live`, `event_start`, `events`, `eventsOnRender`, `from`, `from_id`, `from_kind`, `gallery_data`, `gilded`, `gildings`, `hidden`, `hide_score`, `href_url`, `id`, `ignore_reports`, `imp_pixel`, `impression_id`, `impression_id_str`, `is_blank`, `is_created_from_ads_ui`, `is_crosspostable`, `is_gallery`, `is_meta`, `is_original_content`, `is_reddit_media_domain`, `is_robot_indexable`, `is_self`, `is_survey_ad`, `is_video`, `likes`, `link_flair_background_color`, `link_flair_css_class`, `link_flair_richtext`, `link_flair_template_id`, `link_flair_text`, `link_flair_text_color`, `link_flair_type`, `live_audio`, `location_lat`, `location_long`, `location_name`, `locked`, `media`, `media_embed`, `media_metadata`, `media_only`, `mobile_ad_url`, `mod_note`, `mod_reason_by`, `mod_reason_title`, `mod_reports`, `name`, `no_follow`, `num_comments`, `num_crossposts`, `num_reports`, `original_link`, `outbound_link`, `over_18`, `parent_whitelist_status`, `permalink`, `pinned`, `poll_data`, `post_categories`, `post_hint`, `preview`, `previous_visits`, `priority_id`, `product_ids`, `promo_layout`, `promoted`, `promoted_by`, `promoted_display_name`, `promoted_url`, `pwls`, `quarantine`, `removal_reason`, `removed`, `removed_by`, `removed_by_category`, `report_reasons`, `retrieved_on`, `retrieved_utc`, `rpan_video`, `rte_mode`, `saved`, `score`, `secure_media`, `secure_media_embed`, `selftext`, `selftext_html`, `send_replies`, `show_media`, `sk_ad_network_data`, `spam`, `spoiler`, `steward_reports`, `stickied`, `subcaption`, `subreddit`, `subreddit_id`, `subreddit_name_prefixed`, `subreddit_subscribers`, `subreddit_type`, `suggested_sort`, `third_party_trackers`, `third_party_tracking`, `third_party_tracking_2`, `thumbnail`, `thumbnail_height`, `thumbnail_width`, `title`, `top_awarded_type`, `total_awards_received`, `tournament_data`, `treatment_tags`, `unrepliable_reason`, `ups`, `upvote_ratio`, `url`, `url_overridden_by_dest`, `user_reports`, `view_count`, `visited`, `websocket_url`, `whitelist_status`, `wls`.

## 4. Robust Schema Evolution Process
To ensure total consistency across 20+ years of archives, the pipeline uses a **Fixed Master Schema** approach:

1.  **Master Reference**: We maintain two fixed master schema files: `master_schema_rc.json` and `master_schema_rs.json`. These contain the super-union of all columns that have historically appeared in >20% of archives.
2.  **Strict Promotion**: For **every** month (whether 2005 or 2024), the conversion script forces the exact same set of columns at the top level.
3.  **NULL Padding**: If a master column (like `poll_data`) didn't exist in an older year, it is automatically included as `NULL`, ensuring that the final Parquet dataset has a perfectly uniform schema across all files of the same type.
4.  **Automatic Bundling**: Any field *not* in the master list is automatically safely bundled into `extra_json`. This handles new, rare, or experimental fields without breaking the top-level schema.

## 5. Verification Checklist
When verifying a new Parquet file, ensure:
1. [ ] **Row Count Match**: `COUNT(*)` in Parquet matches the source ZST.
2. [ ] **Search Columns Present**: `author` and `subreddit` exist and are populated.
3. [ ] **Global Order**: The file must be sorted by `author` ASC, then `subreddit` ASC.
4. [ ] **Edited Normalization**: The `edited` column must be `BIGINT`, not `BOOLEAN`.
5. [ ] **Lossless Check**: Randomly sample rows and ensure fields missing from the top level are present in `extra_json`.
