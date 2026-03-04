import React from 'react';
import {
  Body,
  Button,
  Container,
  Head,
  Hr,
  Html,
  Img,
  Link,
  Preview,
  Row,
  Section,
  Text,
  Column,
  Table,
  Tailwind,
} from '@react-email/components';

interface SettlementConfirmationProps {
  batchId: string;
  settlementDate: string;
  totalAmount: string;
  transactionCount: number;
  pspBreakdown: Array<{
    pspName: string;
    transactionCount: number;
    amount: string;
  }>;
  platformFees: string;
  exceptions?: number;
  dashboardUrl: string;
  supportEmail: string;
}

export default function SettlementConfirmation({
  batchId,
  settlementDate,
  totalAmount,
  transactionCount,
  pspBreakdown,
  platformFees,
  exceptions = 0,
  dashboardUrl,
  supportEmail,
}: SettlementConfirmationProps) {
  const formattedDate = new Date(settlementDate).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <Html>
      <Head />
      <Preview>Daily settlement completed - {batchId}</Preview>
      <Tailwind>
        <Body className="bg-white font-sans">
          <Container className="mx-auto py-5 px-5">
            {/* Header */}
            <Section className="py-6 border-b border-gray-200">
              <Row>
                <Column>
                  <Img
                    src="https://fintech-ops.example.com/logo.png"
                    width="32"
                    height="32"
                    alt="Fintech Ops"
                    className="mr-2"
                  />
                  <Text className="inline-block font-bold text-lg">
                    Fintech Operations
                  </Text>
                </Column>
              </Row>
            </Section>

            {/* Main Content */}
            <Section className="py-8">
              <Text className="text-2xl font-bold text-gray-900 m-0">
                Daily Settlement Completed
              </Text>
              <Text className="text-gray-600 mt-2">
                {formattedDate}
              </Text>

              {/* Status Alert */}
              <Section className="bg-green-50 border border-green-200 rounded-lg py-4 px-4 mt-6">
                <Text className="text-green-800 font-semibold m-0">
                  ✓ Settlement successfully submitted to bank
                </Text>
                {exceptions > 0 && (
                  <Text className="text-yellow-700 text-sm mt-2 m-0">
                    {exceptions} exception(s) require manual review
                  </Text>
                )}
              </Section>

              {/* Summary Stats */}
              <Section className="mt-6 bg-gray-50 rounded-lg py-4 px-4">
                <Row>
                  <Column className="w-1/2">
                    <Text className="text-gray-600 text-sm m-0">
                      Total Settled Amount
                    </Text>
                    <Text className="text-2xl font-bold text-gray-900 m-0 mt-1">
                      ${totalAmount}
                    </Text>
                  </Column>
                  <Column className="w-1/2">
                    <Text className="text-gray-600 text-sm m-0">
                      Transaction Count
                    </Text>
                    <Text className="text-2xl font-bold text-gray-900 m-0 mt-1">
                      {transactionCount.toLocaleString()}
                    </Text>
                  </Column>
                </Row>
              </Section>

              {/* PSP Breakdown Table */}
              <Section className="mt-6">
                <Text className="text-lg font-semibold text-gray-900 m-0 mb-3">
                  Settlement by Payment Service Provider
                </Text>
                <Table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-gray-300 bg-gray-100">
                      <th className="text-left py-2 px-3 font-semibold text-sm">
                        PSP
                      </th>
                      <th className="text-right py-2 px-3 font-semibold text-sm">
                        Transactions
                      </th>
                      <th className="text-right py-2 px-3 font-semibold text-sm">
                        Amount
                      </th>
                      <th className="text-right py-2 px-3 font-semibold text-sm">
                        % of Total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {pspBreakdown.map((psp, index) => (
                      <tr
                        key={index}
                        className={
                          index % 2 === 0
                            ? 'bg-white'
                            : 'bg-gray-50'
                        }
                      >
                        <td className="py-3 px-3 text-sm text-gray-900">
                          {psp.pspName}
                        </td>
                        <td className="py-3 px-3 text-sm text-right text-gray-600">
                          {psp.transactionCount.toLocaleString()}
                        </td>
                        <td className="py-3 px-3 text-sm text-right text-gray-900 font-semibold">
                          ${psp.amount}
                        </td>
                        <td className="py-3 px-3 text-sm text-right text-gray-600">
                          {(
                            (parseFloat(psp.amount) / parseFloat(totalAmount)) *
                            100
                          ).toFixed(1)}
                          %
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Section>

              {/* Fees Section */}
              <Section className="mt-6 bg-blue-50 rounded-lg py-4 px-4">
                <Text className="text-gray-800 text-sm m-0">
                  <strong>Platform Fees Collected:</strong>
                </Text>
                <Text className="text-lg font-bold text-blue-600 m-0 mt-1">
                  ${platformFees}
                </Text>
              </Section>

              {/* Details and Action */}
              <Section className="mt-8">
                <Row>
                  <Column>
                    <Button
                      href={dashboardUrl}
                      className="bg-blue-600 text-white px-6 py-3 rounded-lg font-semibold text-center inline-block"
                    >
                      View Settlement Dashboard
                    </Button>
                  </Column>
                </Row>
              </Section>

              {/* Additional Info */}
              <Section className="mt-8 bg-gray-50 rounded-lg py-4 px-4 text-sm">
                <Text className="text-gray-700 m-0 font-semibold">
                  Batch Details
                </Text>
                <Row className="mt-3">
                  <Column className="w-1/2">
                    <Text className="text-gray-600 text-xs m-0">
                      Batch ID
                    </Text>
                    <Text className="text-gray-900 font-mono text-sm m-0 mt-1">
                      {batchId}
                    </Text>
                  </Column>
                  <Column className="w-1/2">
                    <Text className="text-gray-600 text-xs m-0">
                      Settlement Date
                    </Text>
                    <Text className="text-gray-900 text-sm m-0 mt-1">
                      {formattedDate}
                    </Text>
                  </Column>
                </Row>
              </Section>

              {/* Footer Info */}
              <Section className="mt-8">
                <Text className="text-gray-600 text-sm m-0">
                  All transactions have been recorded in the ledger and submitted to
                  the bank. Settlement funds are expected to be received within
                  1-2 business days.
                </Text>

                {exceptions > 0 && (
                  <Text className="text-yellow-700 text-sm mt-4 m-0 font-semibold">
                    ⚠️ This settlement has {exceptions} exception(s) that require
                    investigation. Please review them in the dashboard and contact
                    support if needed.
                  </Text>
                )}

                <Text className="text-gray-600 text-xs mt-6 m-0">
                  Have questions? Contact us at{' '}
                  <Link href={`mailto:${supportEmail}`} className="text-blue-600">
                    {supportEmail}
                  </Link>
                </Text>
              </Section>
            </Section>

            {/* Footer */}
            <Hr className="border-gray-200 my-6" />
            <Section>
              <Text className="text-gray-600 text-xs text-center m-0">
                © {new Date().getFullYear()} Fintech Operations Platform. All rights
                reserved.
              </Text>
              <Text className="text-gray-500 text-xs text-center mt-2 m-0">
                This is an automated email from your settlement system. Please do not
                reply to this address.
              </Text>
            </Section>
          </Container>
        </Body>
      </Tailwind>
    </Html>
  );
}

// Export for use with React Email renderer
export const SettlementConfirmationEmail = SettlementConfirmation;
