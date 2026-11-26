import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors, HttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { App } from './app';
import { facelessHeader, errorText } from './api';
import { ApprovalForm } from './pages/approval-form';
import { JobsPage } from './pages/jobs';
import { HttpErrorResponse } from '@angular/common/http';

function setup(extra: unknown[] = []) {
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      provideHttpClient(withInterceptors([facelessHeader])),
      provideHttpClientTesting(),
      ...extra as [],
    ],
  });
  return TestBed.inject(HttpTestingController);
}

describe('App shell', () => {
  it('renders navigation', async () => {
    setup();
    const f = TestBed.createComponent(App);
    await f.whenStable();
    const text = (f.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Calendar');
    expect(text).toContain('publishing is manual');
  });
});

describe('facelessHeader interceptor', () => {
  it('adds X-Faceless to every request', async () => {
    const http = setup();
    const p = firstValueFrom(TestBed.inject(HttpClient).post('/api/jobs', {}));
    const req = http.expectOne('/api/jobs');
    expect(req.request.headers.get('X-Faceless')).toBe('1');
    req.flush({ id: 'x' });
    await p;
  });
});

describe('errorText', () => {
  it('shows API detail and hint', () => {
    const e = new HttpErrorResponse({ status: 409, error: { detail: 'not approved', hint: 'do X' } });
    expect(errorText(e)).toBe('not approved — do X');
  });
});

describe('ApprovalForm', () => {
  it('stays disabled until reviewer and APPROVE are entered', async () => {
    setup();
    const f = TestBed.createComponent(ApprovalForm);
    f.componentRef.setInput('label', 'Approve');
    await f.whenStable();
    const button = () => (f.nativeElement as HTMLElement).querySelector('button.primary') as HTMLButtonElement;
    expect(button().disabled).toBe(true);
    const emitted: unknown[] = [];
    f.componentInstance.approve.subscribe((a) => emitted.push(a));
    const inputs = (f.nativeElement as HTMLElement).querySelectorAll('input');
    const type = async (el: HTMLInputElement, value: string) => {
      el.value = value;
      el.dispatchEvent(new Event('input'));
      await f.whenStable();
    };
    await type(inputs[0], 'Sam');
    await type(inputs[1], 'approve');
    expect(button().disabled).toBe(true);
    await type(inputs[1], 'APPROVE');
    expect(button().disabled).toBe(false);
    button().click();
    expect(emitted).toEqual([{ confirm: 'APPROVE', reviewer: 'Sam', notes: '', acknowledge_flags: false }]);
  });
});

describe('JobsPage', () => {
  it('lists jobs from the API', async () => {
    const http = setup();
    const f = TestBed.createComponent(JobsPage);
    http.expectOne('/api/jobs').flush([
      { id: 'd001-x', topic: 'Why prices end in 99', day: 1, date: '2027-01-01', state: 'RENDERED',
        failed_stage: null, error: null, created: '', updated: 'now' },
    ]);
    await f.whenStable();
    const text = (f.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Why prices end in 99');
    expect(text).toContain('RENDERED');
  });
});
