/**
 * Tests for URL normalizer.
 * Run: npx tsx --test src/tests/url.test.ts
 */
import { strict as assert } from "node:assert";
import { test, describe } from "node:test";
import { normalizeUrl } from "../normalize/url.js";

describe("normalizeUrl", () => {
  test("canonical URL unchanged", () => {
    const r = normalizeUrl("https://www.linkedin.com/in/satyanadella");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
    assert.equal(r.was_normalized, false);
  });

  test("adds missing protocol", () => {
    const r = normalizeUrl("linkedin.com/in/satyanadella");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
    assert.equal(r.was_normalized, true);
  });

  test("strips country subdomain (in.)", () => {
    const r = normalizeUrl("https://in.linkedin.com/in/satyanadella/");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("strips mobile subdomain (m.)", () => {
    const r = normalizeUrl("https://m.linkedin.com/in/satyanadella");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("strips tracking query params", () => {
    const r = normalizeUrl("https://www.linkedin.com/in/satyanadella/?trk=nav_responsive_tab_profile_pic&something=else");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("lowercases slug", () => {
    const r = normalizeUrl("https://www.linkedin.com/in/SatyaNadella/");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("strips trailing slash", () => {
    const r = normalizeUrl("https://www.linkedin.com/in/satyanadella/");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("handles /pub/ old URL format", () => {
    const r = normalizeUrl("https://www.linkedin.com/pub/satya-nadella/12/345/678");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satya-nadella");
  });

  test("handles sales navigator URL", () => {
    const r = normalizeUrl("https://www.linkedin.com/sales/people/satyanadella,ACwAABcdef");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella,acwaabcdef");
  });

  test("strips extra path segments after slug", () => {
    const r = normalizeUrl("https://www.linkedin.com/in/satyanadella/details/experience/");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("handles missing www", () => {
    const r = normalizeUrl("https://linkedin.com/in/satyanadella");
    assert.equal(r.canonical, "https://www.linkedin.com/in/satyanadella");
  });

  test("throws on bare slug (no domain)", () => {
    // "satyanadella" parses as a valid hostname, so the error is about the profile slug path
    assert.throws(() => normalizeUrl("satyanadella"), /profile slug/);
  });

  test("throws on URL with no profile path", () => {
    assert.throws(() => normalizeUrl("https://www.linkedin.com/company/microsoft"), /profile slug/);
  });

  test("returns slug correctly", () => {
    const r = normalizeUrl("https://www.linkedin.com/in/prakhar-pandey-59a79616a/");
    assert.equal(r.slug, "prakhar-pandey-59a79616a");
  });
});
