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

interface BreakDetail {
  breakType: string;
  count: number;
  totalAmount: string;
  avgAmount: string;
  priority: string;
}

interface ReconciliationAlertProps {
  reconciliationRunId: string;
  reconciliationDate: string;
  totalBreaks: number;
  totalUnmatchedAmount: string;
  matchRate: string;
  topBreaks: BreakDetail[];
  investigateUrl: string;
  supportEmail: string;
  criticality: 'low' | 'medium' | 'high' | 'critical';
}

const getPriorityColor = (priority: string) => {
  switch (priority) {
    case 'critical':
      return 'bg-red-50 border-red-200';
    case 'high':
      return 'bg-orange-50 border-orange-200';
    case 'medium':
      return 'bg-yellow-50 border-yellow-200';
    default:
      return 'bg-blue-50 border-blue-200';
  }
};

const getCriticalityAlert = (criticality: string) => {
  switch (criticality) {
    case 'critical':
      return {
        bg: 'bg-red-50',
        border: 'border-red-200',
        textColor: 'text-red-800',
        icon: '🔴',
        message: 'Critical: Immediate attention required',
      };
    case 'high':
      return {
        bg: 'bg-orange-50',
        border: 'border-orange-200',
        textColor: 'text-orange-800',
        icon: '🟠',
        message: 'High: Urgent attention required',
      };
    case 'medium':
      return {
        bg: 'bg-yellow-50',
        border: 'border-yellow-200',
        textColor: 'text-yellow-800',
        icon: '🟡',
        message: 'Medium: Review soon',
      };
    default:
      return {
        bg: 'bg-blue-50',
        border: 'border-blue-200',
        textColor: 'text-blue-800',
        icon: '🔵',
        message: 'Low: Routine review',
      };
  }
};

export default function ReconciliationAlert({
  reconciliationRunId,
  reconciliationDate,
  totalBreaks,
  totalUnmatchedAmount,
  matchRate,
  topBreaks,
  investigateUrl,
  supportEmail,
  criticality = 'medium',
}: ReconciliationAlertProps) {
  const formattedDate = new Date(reconciliationDate).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  const alert = getCriticalityAlert(criticality);

  return (
    <Html>
      <Head />
      <Preview>Reconciliation Alert - {totalBreaks} breaks detected</Preview>
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
                {alert.icon} Reconciliation Breaks Detected
              </Text>
              <Text className="text-gray-600 mt-2">
                {formattedDate}
              </Text>

              {/* Critical Alert Box */}
              <Section className={`${alert.bg} border ${alert.border} rounded-lg py-4 px-4 mt-6`}>
                <Text className={`${alert.textColor} font-semibold m-0`}>
                  {alert.message}
                </Text>
                <Text className={`${alert.textColor} text-sm mt-2 m-0`}>
                  {totalBreaks} unmatched record(s) totaling ${totalUnmatchedAmount}
                </Text>
              </Section>

              {/* Summary Stats */}
              <Section className="mt-6 bg-gray-50 rounded-lg py-4 px-4">
                <Row>
                  <Column className="w-1/3">
                    <Text className="text-gray-600 text-sm m-0">
                      Total Breaks
                    </Text>
                    <Text className="text-2xl font-bold text-gray-900 m-0 mt-1">
                      {totalBreaks}
                    </Text>
                  </Column>
                  <Column className="w-1/3">
                    <Text className="text-gray-600 text-sm m-0">
                      Unmatched Amount
                    </Text>
                    <Text className="text-2xl font-bold text-gray-900 m-0 mt-1">
                      ${totalUnmatchedAmount}
                    </Text>
                  </Column>
                  <Column className="w-1/3">
                    <Text className="text-gray-600 text-sm m-0">
                      Match Rate
                    </Text>
                    <Text className="text-2xl font-bold text-gray-900 m-0 mt-1">
                      {matchRate}%
                    </Text>
                  </Column>
                </Row>
              </Section>

              {/* Top Breaks Table */}
              <Section className="mt-6">
                <Text className="text-lg font-semibold text-gray-900 m-0 mb-3">
                  Top Break Categories
                </Text>
                <Table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-gray-300 bg-gray-100">
                      <th className="text-left py-2 px-3 font-semibold text-sm">
                        Break Type
                      </th>
                      <th className="text-center py-2 px-3 font-semibold text-sm">
                        Count
                      </th>
                      <th className="text-right py-2 px-3 font-semibold text-sm">
                        Total Amount
                      </th>
                      <th className="text-right py-2 px-3 font-semibold text-sm">
                        Avg Amount
                      </th>
                      <th className="text-center py-2 px-3 font-semibold text-sm">
                        Priority
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {topBreaks.map((breakDetail, index) => (
                      <tr
                        key={index}
                        className={
                          index % 2 === 0
                            ? 'bg-white'
                            : 'bg-gray-50'
                        }
                      >
                        <td className="py-3 px-3 text-sm text-gray-900 font-medium">
                          {breakDetail.breakType}
                        </td>
                        <td className="py-3 px-3 text-sm text-center text-gray-600">
                          {breakDetail.count}
                        </td>
                        <td className="py-3 px-3 text-sm text-right text-gray-900">
                          ${breakDetail.totalAmount}
                        </td>
                        <td className="py-3 px-3 text-sm text-right text-gray-600">
                          ${breakDetail.avgAmount}
                        </td>
                        <td className="py-3 px-3 text-center text-xs">
                          <span
                            className={`inline-block px-2 py-1 rounded font-semibold ${
                              breakDetail.priority === 'critical'
                                ? 'bg-red-100 text-red-700'
                                : breakDetail.priority === 'high'
                                ? 'bg-orange-100 text-orange-700'
                                : breakDetail.priority === 'medium'
                                ? 'bg-yellow-100 text-yellow-700'
                                : 'bg-blue-100 text-blue-700'
                            }`}
                          >
                            {breakDetail.priority}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Section>

              {/* Recommended Actions */}
              <Section className="mt-6 bg-blue-50 border border-blue-200 rounded-lg py-4 px-4">
                <Text className="text-blue-900 font-semibold text-sm m-0">
                  Recommended Actions
                </Text>
                <Text className="text-blue-800 text-sm mt-2 m-0">
                  • Review the break details in the dashboard
                </Text>
                <Text className="text-blue-800 text-sm mt-1 m-0">
                  • Check if auto-resolution patterns were applied
                </Text>
                <Text className="text-blue-800 text-sm mt-1 m-0">
                  • Contact the relevant PSP if breaks are timing-related
                </Text>
                <Text className="text-blue-800 text-sm mt-1 m-0">
                  • Escalate critical breaks for manual investigation
                </Text>
              </Section>

              {/* Investigation Button */}
              <Section className="mt-8">
                <Row>
                  <Column>
                    <Button
                      href={investigateUrl}
                      className="bg-blue-600 text-white px-6 py-3 rounded-lg font-semibold text-center inline-block"
                    >
                      Investigate Breaks
                    </Button>
                  </Column>
                </Row>
              </Section>

              {/* Details Section */}
              <Section className="mt-8 bg-gray-50 rounded-lg py-4 px-4 text-sm">
                <Text className="text-gray-700 m-0 font-semibold">
                  Run Details
                </Text>
                <Row className="mt-3">
                  <Column className="w-1/2">
                    <Text className="text-gray-600 text-xs m-0">
                      Reconciliation Run ID
                    </Text>
                    <Text className="text-gray-900 font-mono text-sm m-0 mt-1">
                      {reconciliationRunId}
                    </Text>
                  </Column>
                  <Column className="w-1/2">
                    <Text className="text-gray-600 text-xs m-0">
                      Reconciliation Date
                    </Text>
                    <Text className="text-gray-900 text-sm m-0 mt-1">
                      {formattedDate}
                    </Text>
                  </Column>
                </Row>
              </Section>

              {/* Next Steps */}
              <Section className="mt-8">
                <Text className="text-gray-600 text-sm m-0">
                  <strong>Next Steps:</strong>
                </Text>
                <Text className="text-gray-600 text-sm mt-2 m-0">
                  1. Log in to the reconciliation dashboard to view detailed break information
                </Text>
                <Text className="text-gray-600 text-sm mt-1 m-0">
                  2. Filter by priority to identify critical items first
                </Text>
                <Text className="text-gray-600 text-sm mt-1 m-0">
                  3. Assign breaks to team members for investigation
                </Text>
                <Text className="text-gray-600 text-sm mt-1 m-0">
                  4. Document findings and update resolution status
                </Text>

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
                This is an automated alert from your reconciliation system. Please do
                not reply to this address.
              </Text>
            </Section>
          </Container>
        </Body>
      </Tailwind>
    </Html>
  );
}

// Export for use with React Email renderer
export const ReconciliationAlertEmail = ReconciliationAlert;
