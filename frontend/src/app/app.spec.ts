import { TestBed } from '@angular/core/testing';
import { HttpClient, HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { App } from './app';
import { errorText, estimateSeconds, facelessHeader } from './api';
import { VideosPage } from './pages/videos';
import { ScriptStep } from './pages/steps/script-step';
import { stateLabel } from './pages/labels';

function setup() {
  TestBed.configureTestingModule({
    providers: [provideRouter([]), provideHttpClient(withInterceptors([facelessHeader])),
                provideHttpClientTesting()],
  });
  return TestBed.inject(HttpTestingController);
}

describe('App shell', () => {
  it('renders the four sections', async () => {
    setup();
    const f = TestBed.createComponent(App);
    await f.whenStable();
    const text = (f.nativeElement as HTMLElement).textContent ?? '';
    for (const s of ['Home', 'Calendar', 'Videos', 'Settings']) expect(text).toContain(s);
  });
});

describe('api helpers', () => {
  it('adds X-Faceless to every request', async () => {
    const http = setup();
    const p = firstValueFrom(TestBed.inject(HttpClient).post('/api/jobs', {}));
    const req = http.expectOne('/api/jobs');
    expect(req.request.headers.get('X-Faceless')).toBe('1');
    req.flush({ id: 'x' });
    await p;
  });

  it('shows API detail and hint', () => {
    const e = new HttpErrorResponse({ status: 409, error: { detail: 'not approved', hint: 'do X' } });
    expect(errorText(e)).toBe('not approved — do X');
    expect(errorText(new HttpErrorResponse({ status: 504 }))).toContain('server is not running');
  });

  it('estimates spoken length like the server', () => {
    const scene = { narration: Array(25).fill('word').join(' ') };
    const est = estimateSeconds(Array(6).fill(scene)); // 150 words
    expect(est).toBeGreaterThan(65);
    expect(est).toBeLessThan(75);
  });

  it('labels states in plain language', () => {
    expect(stateLabel('RESEARCH_REVIEW').text).toBe('Check the script');
    expect(stateLabel('EXPORTED').cls).toBe('ok');
  });
});

describe('VideosPage', () => {
  it('lists videos with plain-language status', async () => {
    const http = setup();
    const f = TestBed.createComponent(VideosPage);
    http.expectOne('/api/jobs').flush([
      { id: 'd001-x', topic: 'Why prices end in 99', day: 1, date: '2026-12-01', state: 'RENDERED',
        failed_stage: null, error: null, created: '', updated: '' },
    ]);
    http.expectOne('/api/settings').flush({ default_subtitles: 'en' });
    await f.whenStable();
    const text = (f.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Why prices end in 99');
    expect(text).toContain('Watch & approve');
  });
});

describe('ScriptStep', () => {
  it('approves with one click (no typing) when the length is right', async () => {
    const http = setup();
    const f = TestBed.createComponent(ScriptStep);
    f.componentRef.setInput('jobId', 'j1');
    const scenes = Array.from({ length: 6 }, (_, i) => ({ id: `s${i}`, narration: Array(25).fill('word').join(' '),
      visual: '', search_terms: [], overlay: '', claim_ids: [] }));
    await f.whenStable();
    http.expectOne('/api/jobs/j1/research').flush({ research: { sources: [] }, problems: [] });
    await Promise.resolve();
    http.expectOne('/api/jobs/j1/script').flush({
      script: { title: 'T', hook: '', narration: '', scenes, claims: [], platform: {}, provider: 'x', provider_meta: {} },
      approved: false, report: { words: 150, est_seconds: 70, errors: [], warnings: [], flagged_claims: [] },
      research_problems: [] });
    await f.whenStable();
    const el = f.nativeElement as HTMLElement;
    f.detectChanges();
    expect(el.textContent).toContain('Good length');
    const btn = el.querySelector('button.primary.big') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(el.querySelector('input[placeholder="Type APPROVE"]')).toBeNull();
    btn.click();
    const req = http.expectOne('/api/jobs/j1/approve-script');
    expect(req.request.body).toEqual({ acknowledge_flags: true });
    req.flush({ approved: true, hash: 'h', task: null });
  });
});
