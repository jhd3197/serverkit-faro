import { useState } from 'react';
import { Modal, copyToClipboard, useToast, useTranslation } from 'serverkit-sdk';

/**
 * Shows a freshly issued faro://grant link — the only time it exists. The
 * panel stores just the token's hash, so closing this without copying means
 * revoking and issuing a new one.
 *
 * `invite` switches the copy to an "add a device" link for a member (and
 * mentions approval when the invite needs it).
 */
export default function GrantLinkModal({ open, link, expiresAt, invite = null, memberName, onClose }) {
    const { t } = useTranslation();
    const toast = useToast();
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        try {
            await copyToClipboard(link);
            setCopied(true);
            toast.success(t('faro.link.copied', 'Link copied'));
        } catch {
            toast.error(t('faro.link.copyFailed', 'Copy failed. Select the link and copy it manually.'));
        }
    };

    const close = () => {
        setCopied(false);
        onClose();
    };

    const when = expiresAt ? new Date(expiresAt).toLocaleString() : '';

    return (
        <Modal open={open} onClose={close}
            title={invite ? t('faro.invite.linkTitle', 'Add-device link') : t('faro.link.title', 'Share this link')}>
            <p>
                {invite
                    ? t('faro.invite.linkIntro', 'Send this to {{name}}. They open it in Faro on the device they want to add, and that device gets every server {{name}} has access to.', { name: memberName })
                    : t('faro.link.intro', 'Send this link to the person you are giving access to. They open it with Faro, review the servers, and click Accept.')}
            </p>
            <textarea
                className="form-input skfaro-link"
                readOnly
                rows={3}
                value={link || ''}
                onFocus={(event) => event.target.select()}
                data-walkthrough="faro-grant-link"
            />
            <div className="skfaro-notice skfaro-notice--warn">
                <strong>{t('faro.link.onceTitle', 'This link is shown once')}</strong>
                <p>
                    {invite?.require_approval
                        ? t('faro.invite.onceBodyApproval', 'It works once and expires {{when}}. The device it adds waits for your approval on the member page before it can connect.', { when })
                        : t('faro.link.onceBody', 'Treat it like a password until it is redeemed: whoever opens it first gets the access. It works once and expires {{when}}.', { when })}
                </p>
            </div>
            <div className="skfaro-actions">
                <button type="button" className="btn btn-primary" onClick={copy}>
                    {copied ? t('faro.link.copiedButton', 'Copied') : t('faro.link.copy', 'Copy link')}
                </button>
                <button type="button" className="btn btn-ghost" onClick={close}>
                    {t('faro.link.done', 'Done')}
                </button>
            </div>
        </Modal>
    );
}
