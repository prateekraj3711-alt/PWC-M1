import 'dotenv/config';
import fs from 'fs/promises';
import path from 'path';
import { chromium } from 'playwright';
import { v4 as uuidv4 } from 'uuid';
import { google } from 'googleapis';
import { attachApiDiscovery } from './discover_api.js';

const PWC_EMAIL = process.env.PWC_USERNAME || process.env.PWC_EMAIL;
const PWC_PASSWORD = process.env.PWC_PASSWORD;

function chromiumLaunchOptions() {
  const o = {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  };
  if (!process.env.PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS) {
    process.env.PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = '1';
  }
  try {
    if (!process.env.CHROMIUM_PATH) {
      const { execSync } = require('child_process');
      const found = execSync(
        'command -v chromium || command -v chromium-browser || command -v google-chrome || true',
        { encoding: 'utf8' }
      ).trim();
      if (found) {
        process.env.CHROMIUM_PATH = found;
      }
    }
    if (process.env.CHROMIUM_PATH && process.env.CHROMIUM_PATH.trim() !== '') {
      o.executablePath = process.env.CHROMIUM_PATH.trim();
    }
  } catch (_) {}
  return o;
}

async function tryFill(page, selectors, value) {
  for (const sel of selectors) {
    try {
      await page.waitForSelector(sel, { timeout: 4000 });
      await page.fill(sel, value);
      return true;
    } catch (_) {
      continue;
    }
  }
  return false;
}

async function tryClick(page, selectors) {
  for (const sel of selectors) {
    try {
      await page.waitForSelector(sel, { timeout: 4000 });
      await page.click(sel);
      return true;
    } catch (_) {
      continue;
    }
  }
  return false;
}

async function findOtpInputInAllFrames(page, totalTimeoutMs = 30000) {
  const otpSelectors = [
    'input[placeholder="One-time verification code"]',
    'input[aria-label="One-time verification code"]',
    'input[autocomplete="one-time-code"]',
    'input[type="text"][inputmode="numeric"]',
    'input[type="tel"]',
    'input[name="callback_2"]',
    'input[name*="otp" i]',
    'input[id*="otp" i]',
    'input[name*="code" i]',
    'input[id*="code" i]',
    'input[aria-label*="verification" i]',
    'input[aria-label*="one-time" i]',
    'input[placeholder*="verification" i]',
    'input[type="text"]',
    'input[type="tel"]'
  ];

  const submitSelectors = [
    'button:has-text("Send my code")',
    'button:has-text("Email me a code")',
    'button:has-text("Send code")',
    'button:has-text("Send verification code")',
    'button:has-text("Submit")',
    'button:has-text("Continue")',
    'button:has-text("Next")',
    'input[type="submit"]',
    'button[type="submit"]'
  ];

  const startTime = Date.now();
  const allFrames = [page, ...page.frames()];
  
  while (Date.now() - startTime < totalTimeoutMs) {
    for (const frame of allFrames) {
      for (const selector of otpSelectors) {
        try {
          const locator = frame.locator(selector).first();
          const isVisible = await locator.isVisible({ timeout: 2000 }).catch(() => false);
          
          if (isVisible) {
            const hasSubmit = await Promise.race(
              submitSelectors.map(s => 
                frame.locator(s).first().isVisible({ timeout: 1000 }).catch(() => false)
              )
            ).catch(() => false);
            
            if (hasSubmit) {
              return { frame, locator };
            }
            
            const hasSubmitAnywhere = await page.locator(submitSelectors.join(', ')).first().isVisible({ timeout: 1000 }).catch(() => false);
            if (hasSubmitAnywhere || frame === page) {
              return { frame, locator };
            }
          }
        } catch (e) {
          continue;
        }
      }
      
      try {
        const labelAnchored = frame.locator('text=/One-time verification code/i').first();
        const labelExists = await labelAnchored.isVisible({ timeout: 1000 }).catch(() => false);
        if (labelExists) {
          const nearbyInput = labelAnchored.locator('..').locator('input').first();
          const inputVisible = await nearbyInput.isVisible({ timeout: 1000 }).catch(() => false);
          if (inputVisible) {
            return { frame, locator: nearbyInput };
          }
        }
      } catch (e) {
        continue;
      }
    }
    
    await page.waitForTimeout(1000);
  }
  
  return null;
}

async function ensureTmpDir() {
  const dirsToTry = [
    path.join('/tmp', 'pwc'),
    path.join(process.cwd(), 'tmp', 'pwc')
  ];
  
  for (const dir of dirsToTry) {
    try {
      await fs.mkdir(dir, { recursive: true });
      await fs.access(dir);
      return dir;
    } catch (err) {
      continue;
    }
  }
  
  throw new Error('Could not create or access tmp directory');
}

async function getSessionPath(sessionId) {
  const baseDir = await ensureTmpDir();
  return path.join(baseDir, `${sessionId}.json`);
}

async function getGmailOtp() {
  const clientId = process.env.GMAIL_CLIENT_ID;
  const clientSecret = process.env.GMAIL_CLIENT_SECRET;
  const refreshToken = process.env.GMAIL_REFRESH_TOKEN;
  const label = process.env.GMAIL_POLL_LABEL || 'INBOX';
  
  if (!clientId || !clientSecret || !refreshToken) {
    throw new Error('Missing Gmail API credentials (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN)');
  }

  const oauth2Client = new google.auth.OAuth2(clientId, clientSecret);
  oauth2Client.setCredentials({ refresh_token: refreshToken });
  const gmail = google.gmail({ version: 'v1', auth: oauth2Client });

  // Search for OTP emails from last 10 minutes
  const q = 'newer_than:10m ("OTP" OR "verification" OR "code" OR "one-time")';
  const res = await gmail.users.messages.list({ userId: 'me', q, labelIds: [label], maxResults: 5 });
  
  if (!res.data.messages || res.data.messages.length === 0) {
    throw new Error('No OTP email found in last 10 minutes');
  }

  // Get the most recent message
  const msg = res.data.messages[0];
  const full = await gmail.users.messages.get({ userId: 'me', id: msg.id, format: 'full' });
  
  // Extract text from email body
  const parts = [full.data.snippet || ''];
  const payload = full.data.payload;
  const stack = [payload];
  
  while (stack.length) {
    const p = stack.pop();
    if (!p) continue;
    if (p.body?.data) {
      const buf = Buffer.from(p.body.data, 'base64');
      parts.push(buf.toString('utf-8'));
    }
    if (p.parts) {
      stack.push(...p.parts);
    }
  }
  
  const text = parts.join('\n');
  
  // Extract 6-digit OTP code
  const m = text.match(/\b(\d{6})\b/);
  if (!m) {
    throw new Error('OTP code not found in email body (expected 6-digit code)');
  }
  
  return m[1];
}

export async function loginAndDiscover() {
  if (!PWC_EMAIL || !PWC_PASSWORD) {
    throw new Error('Missing PWC_USERNAME/PWC_EMAIL or PWC_PASSWORD');
  }

  const browser = await chromium.launch(chromiumLaunchOptions());
  const context = await browser.newContext();
  const page = await context.newPage();
  const sessionId = uuidv4();
  const stateToken = uuidv4();
  const tmp = await ensureTmpDir();
  const sessionPath = await getSessionPath(sessionId);
  
  // Attach API discovery
  const saveDiscovery = attachApiDiscovery(page, sessionId, tmp);

  try {
    // Navigate to login page
    await page.goto(`https://login.pwc.com/login/?goto=https:%2F%2Flogin.pwc.com:443%2Fopenam%2Foauth2%2Fauthorize%3Fresponse_type%3Dcode%26client_id%3Durn%253Acompliancenominationportal.in.pwc.com%26redirect_uri%3Dhttps%253A%252F%252Fcompliancenominationportal.in.pwc.com%26scope%3Dopenid%26state%3D${stateToken}&realm=%2Fpwc`, { timeout: 60000 });

    // Fill email
    const emailFilled = await tryFill(page, [
      'input[name="callback_0"]',
      'input[type="email"]',
      'input[name="email" i]'
    ], PWC_EMAIL);
    
    if (!emailFilled) {
      throw new Error('Email field not found');
    }

    // Click Next/Continue if needed
    await tryClick(page, [
      'button:has-text("Next")',
      'button:has-text("Continue")',
      'input[type="submit"][value*="Next" i]',
      'input[type="submit"][value*="Continue" i]'
    ]);

    // Fill password
    const passFilled = await tryFill(page, [
      'input[name="callback_1"]',
      'input[name="IDToken2"]',
      'input#password',
      'input[name="password" i]',
      'input[autocomplete="current-password"]',
      'input[type="password"]'
    ], PWC_PASSWORD);
    
    if (!passFilled) {
      throw new Error('Password field not found');
    }

    // Submit login
    const submitted = await tryClick(page, [
      'button[type="submit"]',
      'input[type="submit"]',
      'button:has-text("Sign in")',
      'button:has-text("Log in")'
    ]);
    
    if (!submitted) {
      throw new Error('Submit button not found');
    }

    await page.waitForTimeout(3000);

    // MFA Selection - Choose Email
    try {
      await page.waitForSelector('text=Choose one of the following options', { timeout: 20000 });

      const emailOption = page.locator('text=/Email me at/i').first();
      await emailOption.click({ force: true });

      // Click "Send my code" button (wait for it to be enabled)
      const frames = [page.mainFrame(), ...page.frames()];
      let clicked = false;

      for (const frame of frames) {
        const btnSelectors = [
          'button:has-text("Send my code")',
          'button:has-text("Send code")',
          'input[value*="Send my code" i]',
          'input[value*="Send code" i]'
        ];

        for (const sel of btnSelectors) {
          try {
            const btn = frame.locator(sel).first();
            const count = await btn.count().catch(() => 0);
            if (count > 0) {
              await btn.waitFor({ state: 'attached', timeout: 5000 }).catch(() => {});
              
              // Wait for button to be enabled (not disabled)
              for (let i = 0; i < 10; i++) {
                const disabled = await btn.getAttribute('disabled').catch(() => null);
                if (!disabled) {
                  await btn.click({ force: true });
                  clicked = true;
                  break;
                }
                await page.waitForTimeout(1000);
              }
              
              if (clicked) break;
            }
          } catch (e) {
            continue;
          }
        }
        if (clicked) break;
      }

      if (!clicked) {
        // JavaScript fallback
        const jsClicked = await page.evaluate(() => {
          const btns = Array.from(document.querySelectorAll('button, input[type=submit]'));
          const target = btns.find(b => /send.*code/i.test(b.textContent || b.value || ''));
          if (target) {
            target.click();
            return true;
          }
          return false;
        });
        if (!jsClicked) {
          throw new Error('Could not find or click Send my code button');
        }
      }

      await page.waitForSelector('input[type="text"], input[type="tel"], input[placeholder*="code" i]', { timeout: 30000 });
    } catch (err) {
      const scr = await page.screenshot({ fullPage: true, type: 'png' }).catch(() => null);
      throw new Error(`MFA selection failed: ${err.message}`);
    }

    await page.waitForTimeout(2000);

    // Find OTP input field
    let found = null;
    try {
      found = await findOtpInputInAllFrames(page, 30000);
      if (!found) {
        throw new Error('OTP field not found in any frame after 30s');
      }
    } catch (e) {
      const scr = await page.screenshot({ fullPage: true, type: 'png' }).catch(() => null);
      throw new Error(`OTP input not found: ${e.message}`);
    }

    // Get OTP from Gmail API
    let otp = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        otp = await getGmailOtp();
        console.log(`[Gmail OTP] Successfully fetched OTP (attempt ${attempt + 1})`);
        break;
      } catch (e) {
        console.warn(`[Gmail OTP] Attempt ${attempt + 1} failed: ${e.message}`);
        if (attempt < 2) {
          await new Promise(resolve => setTimeout(resolve, 5000));
        } else {
          throw new Error(`Failed to fetch OTP from Gmail after 3 attempts: ${e.message}`);
        }
      }
    }

    if (!otp) {
      throw new Error('OTP not retrieved from Gmail');
    }

    // Fill OTP
    await found.locator.fill(otp);

    // Submit OTP
    const submitSelectors = [
      'button:has-text("Submit")',
      'button:has-text("Continue")',
      'button:has-text("Verify")',
      'button[type="submit"]',
      'input[type="submit"]',
      'button:has-text("Send my code")',
      'button:has-text("Email me a code")'
    ];
    
    let submitted = false;
    for (const sel of submitSelectors) {
      try {
        const submitBtn = found.frame.locator(sel).first();
        if (await submitBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
          await submitBtn.click();
          submitted = true;
          break;
        }
      } catch (_) {
        continue;
      }
    }
    
    if (!submitted) {
      for (const sel of submitSelectors) {
        try {
          const submitBtn = page.locator(sel).first();
          if (await submitBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
            await submitBtn.click();
            submitted = true;
            break;
          }
        } catch (_) {
          continue;
        }
      }
    }
    
    if (!submitted) {
      throw new Error('Submit button not found after OTP entry');
    }

    await page.waitForLoadState('networkidle', { timeout: 30000 });
    await page.waitForTimeout(2000);

    // Verify login success
    const currentUrl = page.url();
    const pageTitle = await page.title().catch(() => '');
    const cookies = await context.cookies();
    
    let loginSuccess = false;
    const successIndicators = [];

    if (currentUrl.includes('compliancenomination') || currentUrl.includes('pwc.com')) {
      successIndicators.push('URL matches PwC domain');
    }

    const selectorsToTry = [
      { sel: 'table', name: 'table element' },
      { sel: '#dashboard', name: 'dashboard element' },
      { sel: 'text="Background Verification"', name: 'Background Verification text' },
      { sel: 'body', name: 'page body' }
    ];

    for (const { sel, name } of selectorsToTry) {
      try {
        await page.waitForSelector(sel, { timeout: 3000 });
        successIndicators.push(`Found ${name}`);
        if (sel !== 'body') {
          loginSuccess = true;
          break;
        }
      } catch (e) {
        continue;
      }
    }

    if (!loginSuccess && cookies.length > 0) {
      const hasAuthCookie = cookies.some(c => 
        c.name.includes('session') || 
        c.name.includes('token') || 
        c.name.includes('auth') ||
        c.domain.includes('pwc.com')
      );
      if (hasAuthCookie) {
        loginSuccess = true;
        successIndicators.push('Auth cookies present');
      }
    }

    if (!loginSuccess && (currentUrl.includes('pwc.com') || currentUrl.includes('compliancenomination'))) {
      const bodyText = await page.textContent('body').catch(() => '');
      if (bodyText && bodyText.length > 100 && !bodyText.toLowerCase().includes('sign in') && !bodyText.toLowerCase().includes('login')) {
        loginSuccess = true;
        successIndicators.push('Page content suggests logged-in state');
      }
    }

    if (!loginSuccess) {
      const scr = await page.screenshot({ fullPage: true, type: 'png' }).catch(() => null);
      throw new Error('Login incomplete - could not verify success');
    }

    // API Discovery - Navigate to dashboard and capture API calls
    console.log('[API Discovery] Navigating to dashboard to capture API traffic...');
    await page.goto('https://compliancenominationportal.in.pwc.com/BGVAdmin/BGVDashboard', {
      waitUntil: 'networkidle',
      timeout: 60000
    });
    
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
    console.log('[API Discovery] Waiting 10 seconds for all API calls to complete...');
    await new Promise(resolve => setTimeout(resolve, 10000));

    // Try clicking "Advance search" to trigger export-related API calls
    try {
      const advanceSearchSelectors = [
        'text=/Advance.*search/i',
        'text=/Advanced.*search/i',
        'button:has-text("Advance")',
        'a:has-text("Advance")',
        '#advanceSearch',
        '[data-action*="advance"]'
      ];
      
      for (const sel of advanceSearchSelectors) {
        try {
          await page.waitForSelector(sel, { timeout: 5000 });
          await page.click(sel, { force: true });
          console.log('[API Discovery] Clicked Advance Search to trigger export APIs');
          await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
          await new Promise(resolve => setTimeout(resolve, 5000));
          break;
        } catch (e) {
          continue;
        }
      }
    } catch (advanceErr) {
      console.warn('[API Discovery] Could not click Advance Search (non-critical):', advanceErr.message);
    }

    // Save storage_state
    const storageState = await context.storageState();
    await fs.writeFile(sessionPath, JSON.stringify(storageState), 'utf-8');
    console.log(`[Session] ✅ Saved complete login session to: ${sessionPath} (${storageState?.cookies?.length || 0} cookies)`);
    
    // Save API map
    const apiMapPath = await saveDiscovery();
    console.log(`[API Discovery] ✅ Saved API map to: ${apiMapPath}`);

    return { sessionId, sessionPath, apiMapPath, storageState };
  } finally {
    await browser.close().catch(() => {});
  }
}

if (process.argv[1] && process.argv[1].endsWith('login.js')) {
  loginAndDiscover().then((info) => {
    console.log(JSON.stringify({ ok: true, ...info }, null, 2));
  }).catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

