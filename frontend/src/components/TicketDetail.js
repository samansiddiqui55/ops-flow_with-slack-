// // // // import React from "react";
// // // // import StatusBadge from "./StatusBadge";

// // // // function TicketDetail({ ticket }) {
// // // //   if (!ticket) {
// // // //     return (
// // // //       <div className="ticket-detail empty-detail">
// // // //         <p>Select a Ticket to View Details</p>
// // // //       </div>
// // // //     );
// // // //   }

// // // //   return (
// // // //     <div className="ticket-detail">
// // // //       <div className="detail-header">
// // // //         <div>
// // // //           <h2>{ticket.id}</h2>
// // // //           <p>{ticket.summary}</p>
// // // //         </div>
// // // //         <StatusBadge status={ticket.status} />
// // // //       </div>

// // // //       <div className="detail-grid">
// // // //         <div><strong>Brand:</strong> {ticket.brand}</div>
// // // //         <div><strong>Sender Email:</strong> {ticket.senderEmail}</div>
// // // //         <div><strong>Source:</strong> {ticket.source}</div>
// // // //         <div><strong>AWB / Tracking ID:</strong> {ticket.awb}</div>
// // // //         <div><strong>Assigned To:</strong> {ticket.assignee || "Unassigned"}</div>
// // // //         <div><strong>Created At:</strong> {ticket.createdAt}</div>
// // // //       </div>

// // // //       <div className="detail-section">
// // // //         <h3>Full Message</h3>
// // // //         <p>{ticket.fullMessage}</p>
// // // //       </div>

// // // //       <div className="detail-section">
// // // //         <h3>Latest Jira Comment</h3>
// // // //         <p>{ticket.latestComment}</p>
// // // //       </div>

// // // //       <div className="detail-actions">
// // // //         <a
// // // //           href={ticket.jiraUrl}
// // // //           target="_blank"
// // // //           rel="noopener noreferrer"
// // // //           className="jira-button"
// // // //         >
// // // //           Open in Jira
// // // //         </a>
// // // //       </div>
// // // //     </div>
// // // //   );
// // // // }

// // // // export default TicketDetail;

// // // // import React from "react";
// // // // import StatusBadge from "./StatusBadge";

// // // // function TicketDetail({ ticket, onResolve }) {
// // // //   if (!ticket) {
// // // //     return (
// // // //       <div className="ticket-detail empty-detail" data-testid="empty-ticket-detail">
// // // //         <p>Select a Ticket to View Details</p>
// // // //       </div>
// // // //     );
// // // //   }

// // // //   // Display Jira issue key if available, otherwise show truncated ticket ID
// // // //   const displayId = ticket.jira_issue_key || `TICKET-${(ticket.id || "").substring(0, 8)}`;
  
// // // //   // Build proper Jira URL: https://grow-simplee.atlassian.net/browse/{ISSUE_KEY}
// // // //   const jiraBaseUrl = "https://grow-simplee.atlassian.net";
// // // //   const jiraUrl = ticket.jira_issue_key 
// // // //     ? `${jiraBaseUrl}/browse/${ticket.jira_issue_key}`
// // // //     : ticket.jira_url || "#";

// // // //   return (
// // // //     <div className="ticket-detail" data-testid="ticket-detail">
// // // //       <div className="detail-header">
// // // //         <div>
// // // //           <h2 data-testid="ticket-detail-id">{displayId}</h2>
// // // //           <p className="detail-subtitle">{ticket.summary}</p>
// // // //         </div>
// // // //         <StatusBadge status={ticket.status} />
// // // //       </div>

// // // //       <div className="detail-grid">
// // // //         <p><strong>Brand:</strong> {ticket.brand}</p>
// // // //         <p><strong>Sender Email:</strong> {ticket.sender_email}</p>
// // // //         <p><strong>Source:</strong> {ticket.source}</p>
// // // //         <p><strong>Issue Type:</strong> {ticket.issue_type || "Other"}</p>
// // // //         <p><strong>AWB / Tracking ID:</strong> {ticket.awb || "N/A"}</p>
// // // //         <p><strong>Assigned To:</strong> {ticket.assigned_to || "Unassigned"}</p>
// // // //         <p><strong>Created At:</strong> {ticket.created_at ? new Date(ticket.created_at).toLocaleString() : "N/A"}</p>
// // // //       </div>

// // // //       <div className="detail-section">
// // // //         <h3>Full Message</h3>
// // // //         <p className="message-content" data-testid="ticket-full-message">
// // // //           {ticket.display_message || ticket.full_message}
// // // //         </p>
// // // //       </div>

// // // //       <div className="detail-section">
// // // //         <h3>Latest Jira Comment</h3>
// // // //         <p>{ticket.latest_comment || "No comments yet."}</p>
// // // //       </div>

// // // //       {ticket.activity_history && ticket.activity_history.length > 0 && (
// // // //         <div className="detail-section" data-testid="activity-history-section">
// // // //           <h3>Activity History {ticket.reopen_count ? `(reopened ${ticket.reopen_count}×)` : ""}</h3>
// // // //           <ul className="activity-list" data-testid="activity-list">
// // // //             {ticket.activity_history.slice(-10).reverse().map((a, idx) => (
// // // //               <li key={idx} className={`activity-item activity-${a.event}`}>
// // // //                 <span className="activity-time">
// // // //                   {a.timestamp ? new Date(a.timestamp).toLocaleString() : ""}
// // // //                 </span>
// // // //                 <span className="activity-event">{a.event}</span>
// // // //                 {a.actor ? <span className="activity-actor"> · {a.actor}</span> : null}
// // // //                 {a.message ? <div className="activity-message">{a.message}</div> : null}
// // // //               </li>
// // // //             ))}
// // // //           </ul>
// // // //         </div>
// // // //       )}

// // // //       <div className="detail-actions">
// // // //         {ticket.jira_issue_key && (
// // // //           <a
// // // //             href={jiraUrl}
// // // //             target="_blank"
// // // //             rel="noreferrer"
// // // //             className="jira-btn"
// // // //             data-testid="open-jira-btn"
// // // //           >
// // // //             Open in Jira ({ticket.jira_issue_key})
// // // //           </a>
// // // //         )}

// // // //         {ticket.status !== "resolved" && (
// // // //           <button 
// // // //             className="resolve-btn" 
// // // //             onClick={() => onResolve(ticket)}
// // // //             data-testid="resolve-ticket-btn"
// // // //           >
// // // //             Resolve Ticket
// // // //           </button>
// // // //         )}
// // // //       </div>
// // // //     </div>
// // // //   );
// // // // }

// // // // export default TicketDetail;

// // // import React, { useState } from "react";
// // // import StatusBadge from "./StatusBadge";
// // // import { replyToSlack } from "../services/api";

// // // function TicketDetail({ ticket, onResolve }) {
// // //   const [reply, setReply] = useState("");
// // //   const [sending, setSending] = useState(false);

// // //   if (!ticket) {
// // //     return (
// // //       <div className="ticket-detail empty-detail">
// // //         <p>Select a Ticket to View Details</p>
// // //       </div>
// // //     );
// // //   }

// // //   const displayId =
// // //     ticket.jira_issue_key ||
// // //     `TICKET-${(ticket.id || "").substring(0, 8)}`;

// // //   const jiraBaseUrl = "https://grow-simplee.atlassian.net";

// // //   const jiraUrl = ticket.jira_issue_key
// // //     ? `${jiraBaseUrl}/browse/${ticket.jira_issue_key}`
// // //     : ticket.jira_url || "#";

// // //   const handleReply = async () => {
// // //     if (!reply.trim()) return;

// // //     try {
// // //       setSending(true);

// // //       await replyToSlack(ticket.id, reply);

// // //       alert("Reply sent to Slack thread");
// // //       setReply("");
// // //     } catch (e) {
// // //       console.error(e);
// // //       alert("Failed sending reply");
// // //     } finally {
// // //       setSending(false);
// // //     }
// // //   };

// // //   return (
// // //     <div className="ticket-detail">

// // //       <div className="detail-header">
// // //         <div>
// // //           <h2>{displayId}</h2>
// // //           <p>{ticket.summary}</p>
// // //         </div>

// // //         <StatusBadge status={ticket.status} />
// // //       </div>


// // //       <div className="detail-grid">
// // //         <p><strong>Brand:</strong> {ticket.brand}</p>
// // //         <p><strong>Sender:</strong> {ticket.sender_email}</p>
// // //         <p><strong>Source:</strong> {ticket.source}</p>
// // //         <p><strong>Issue:</strong> {ticket.issue_type}</p>
// // //       </div>


// // //       <div className="detail-section">
// // //         <h3>Full Message</h3>

// // //         <p>
// // //           {ticket.display_message || ticket.full_message}
// // //         </p>
// // //       </div>


// // //       {ticket.activity_history?.length > 0 && (
// // //         <div className="detail-section">

// // //           <h3>Activity History</h3>

// // //           {ticket.activity_history
// // //             .slice(-10)
// // //             .reverse()
// // //             .map((a, idx) => (

// // //               <div key={idx}>

// // //                 <strong>{a.actor}</strong>

// // //                 <div>{a.message}</div>

// // //               </div>
// // //           ))}
// // //         </div>
// // //       )}


// // //       {/* NEW FEATURE */}
// // //       <div className="detail-section">

// // //         <h3>Reply in Slack Thread</h3>

// // //         <textarea
// // //           value={reply}
// // //           onChange={(e)=>setReply(e.target.value)}
// // //           placeholder="Type reply..."
// // //           rows={4}
// // //           style={{width:"100%"}}
// // //         />

// // //         <button
// // //           onClick={handleReply}
// // //           disabled={sending}
// // //           className="secondary-btn"
// // //         >
// // //           {sending ? "Sending..." : "Send Reply"}
// // //         </button>

// // //       </div>


// // //       <div className="detail-actions">

// // //         {ticket.jira_issue_key && (
// // //           <a
// // //             href={jiraUrl}
// // //             target="_blank"
// // //             rel="noreferrer"
// // //             className="jira-btn"
// // //           >
// // //             Open Jira
// // //           </a>
// // //         )}

// // //         {ticket.status !== "resolved" && (

// // //           <button
// // //             onClick={() => onResolve(ticket)}
// // //             className="resolve-btn"
// // //           >
// // //             Resolve Ticket
// // //           </button>

// // //         )}

// // //       </div>

// // //     </div>
// // //   );
// // // }

// // // export default TicketDetail;


// // import React, { useState } from "react";
// // import StatusBadge from "./StatusBadge";
// // import { replyToSlack } from "../services/api";

// // function TicketDetail({ ticket, onResolve }) {
// //   const [reply, setReply] = useState("");
// //   const [sending, setSending] = useState(false);

// //   const handleReply = async () => {
// //     if (!reply.trim()) return;

// //     try {
// //       setSending(true);

// //       await replyToSlack(ticket.id, reply);

// //       alert("Reply sent to Slack");
// //       setReply("");
// //     } catch (e) {
// //       console.error(e);
// //       alert("Failed sending reply");
// //     } finally {
// //       setSending(false);
// //     }
// //   };

// //   if (!ticket) {
// //     return (
// //       <div
// //         className="ticket-detail empty-detail"
// //         data-testid="empty-ticket-detail"
// //       >
// //         <p>Select a Ticket to View Details</p>
// //       </div>
// //     );
// //   }

// //   const displayId =
// //     ticket.jira_issue_key ||
// //     `TICKET-${(ticket.id || "").substring(0, 8)}`;

// //   const jiraBaseUrl = "https://grow-simplee.atlassian.net";

// //   const jiraUrl = ticket.jira_issue_key
// //     ? `${jiraBaseUrl}/browse/${ticket.jira_issue_key}`
// //     : ticket.jira_url || "#";

// //   return (
// //     <div className="ticket-detail" data-testid="ticket-detail">

// //       <div className="detail-header">
// //         <div>
// //           <h2 data-testid="ticket-detail-id">{displayId}</h2>

// //           <p className="detail-subtitle">
// //             {ticket.summary}
// //           </p>
// //         </div>

// //         <StatusBadge status={ticket.status} />
// //       </div>


// //       <div className="detail-grid">
// //         <p><strong>Brand:</strong> {ticket.brand}</p>

// //         <p><strong>Sender Email:</strong> {ticket.sender_email}</p>

// //         <p><strong>Source:</strong> {ticket.source}</p>

// //         <p>
// //           <strong>Issue Type:</strong>{" "}
// //           {ticket.issue_type || "Other"}
// //         </p>

// //         <p>
// //           <strong>AWB / Tracking ID:</strong>{" "}
// //           {ticket.awb || "N/A"}
// //         </p>

// //         <p>
// //           <strong>Assigned To:</strong>{" "}
// //           {ticket.assigned_to || "Unassigned"}
// //         </p>

// //         <p>
// //           <strong>Created At:</strong>{" "}
// //           {ticket.created_at
// //             ? new Date(ticket.created_at).toLocaleString()
// //             : "N/A"}
// //         </p>
// //       </div>


// //       <div className="detail-section">
// //         <h3>Full Message</h3>

// //         <p
// //           className="message-content"
// //           data-testid="ticket-full-message"
// //         >
// //           {ticket.display_message || ticket.full_message}
// //         </p>
// //       </div>


// //       <div className="detail-section">
// //         <h3>Latest Jira Comment</h3>

// //         <p>
// //           {ticket.latest_comment || "No comments yet."}
// //         </p>
// //       </div>


// //       {ticket.activity_history &&
// //         ticket.activity_history.length > 0 && (

// //         <div
// //           className="detail-section"
// //           data-testid="activity-history-section"
// //         >

// //           <h3>
// //             Activity History{" "}
// //             {ticket.reopen_count
// //               ? `(reopened ${ticket.reopen_count}×)`
// //               : ""}
// //           </h3>

// //           <ul
// //             className="activity-list"
// //             data-testid="activity-list"
// //           >

// //             {ticket.activity_history
// //               .slice(-10)
// //               .reverse()
// //               .map((a, idx) => (

// //               <li
// //                 key={idx}
// //                 className={`activity-item activity-${a.event}`}
// //               >

// //                 <span className="activity-time">
// //                   {a.timestamp
// //                     ? new Date(a.timestamp).toLocaleString()
// //                     : ""}
// //                 </span>

// //                 <span className="activity-event">
// //                   {a.event}
// //                 </span>

// //                 {a.actor && (
// //                   <span className="activity-actor">
// //                     {" "}
// //                     · {a.actor}
// //                   </span>
// //                 )}

// //                 {a.message && (
// //                   <div className="activity-message">
// //                     {a.message}
// //                   </div>
// //                 )}

// //               </li>
// //             ))}
// //           </ul>

// //         </div>
// //       )}


// //       {/* NEW FEATURE */}
// //       <div className="detail-section">

// //         <h3>Reply to Slack Thread</h3>

// //         <textarea
// //           value={reply}
// //           onChange={(e) => setReply(e.target.value)}
// //           placeholder="Type reply here..."
// //           rows={3}
// //           className="message-content"
// //           style={{
// //             width: "100%",
// //             resize: "vertical"
// //           }}
// //         />

// //         <button
// //           className="secondary-btn"
// //           onClick={handleReply}
// //           disabled={sending}
// //           style={{ marginTop: "10px" }}
// //         >
// //           {sending ? "Sending..." : "Send Reply"}
// //         </button>

// //       </div>


// //       <div className="detail-actions">

// //         {ticket.jira_issue_key && (
// //           <a
// //             href={jiraUrl}
// //             target="_blank"
// //             rel="noreferrer"
// //             className="jira-btn"
// //             data-testid="open-jira-btn"
// //           >
// //             Open in Jira ({ticket.jira_issue_key})
// //           </a>
// //         )}

// //         {ticket.status !== "resolved" && (

// //           <button
// //             className="resolve-btn"
// //             onClick={() => onResolve(ticket)}
// //             data-testid="resolve-ticket-btn"
// //           >
// //             Resolve Ticket
// //           </button>

// //         )}

// //       </div>

// //     </div>
// //   );
// // }

// // export default TicketDetail;

// import React from "react";
// import StatusBadge from "./StatusBadge";

// function TicketDetail({ ticket, onResolve }) {
//   if (!ticket) {
//     return (
//       <div className="ticket-detail empty-detail">
//         <p>Select a Ticket to View Details</p>
//       </div>
//     );
//   }

//   const displayId =
//     ticket.jira_issue_key ||
//     `TICKET-${(ticket.id || "").substring(0, 8)}`;

//   const jiraBaseUrl =
//     "https://grow-simplee.atlassian.net";

//   const jiraUrl = ticket.jira_issue_key
//     ? `${jiraBaseUrl}/browse/${ticket.jira_issue_key}`
//     : ticket.jira_url || "#";

//   return (
//     <div className="ticket-detail">

//       <div className="detail-header">
//         <div>
//           <h2>{displayId}</h2>
//           <p className="detail-subtitle">
//             {ticket.summary}
//           </p>
//         </div>

//         <StatusBadge status={ticket.status} />
//       </div>


//       <div className="detail-grid">

//         <p>
//           <strong>Brand:</strong>{" "}
//           {ticket.brand}
//         </p>

//         <p>
//           <strong>Sender Email:</strong>{" "}
//           {ticket.sender_email}
//         </p>

//         <p>
//           <strong>Source:</strong>{" "}
//           {ticket.source}
//         </p>

//         <p>
//           <strong>Issue Type:</strong>{" "}
//           {ticket.issue_type || "Other"}
//         </p>

//         <p>
//           <strong>AWB / Tracking ID:</strong>{" "}
//           {ticket.awb || "N/A"}
//         </p>

//         <p>
//           <strong>Assigned To:</strong>{" "}
//           {ticket.assigned_to || "Unassigned"}
//         </p>

//         <p>
//           <strong>Created At:</strong>{" "}
//           {ticket.created_at
//             ? new Date(
//                 ticket.created_at
//               ).toLocaleString()
//             : "N/A"}
//         </p>

//       </div>



//       <div className="detail-section">

//         <h3>Full Message</h3>

//         <p className="message-content">
//           {ticket.display_message ||
//             ticket.full_message}
//         </p>

//       </div>



//       <div className="detail-section">

//         <h3>Latest Jira Comment</h3>

//         <p>
//           {ticket.latest_comment ||
//             "No comments yet."}
//         </p>

//       </div>



//       {ticket.activity_history &&
//         ticket.activity_history.length >
//           0 && (

//           <div className="detail-section">

//             <h3>
//               Activity History{" "}
//               {ticket.reopen_count
//                 ? `(reopened ${ticket.reopen_count}×)`
//                 : ""}
//             </h3>

//             <ul
//               style={{
//                 listStyle: "none",
//                 padding: 0
//               }}
//             >

//               {ticket.activity_history
//                 .slice(-10)
//                 .reverse()
//                 .map((a, idx) => (

//                   <li
//                     key={idx}
//                     style={{
//                       marginBottom:
//                         "12px",
//                       padding:
//                         "12px",
//                       borderLeft:
//                         "3px solid #35baf6",
//                       background:
//                         "#1b2133",
//                       borderRadius:
//                         "8px"
//                     }}
//                   >

//                     <small>
//                       {a.timestamp
//                         ? new Date(
//                             a.timestamp
//                           ).toLocaleString()
//                         : ""}
//                     </small>

//                     <strong>
//                       {" "}
//                       {a.event}
//                     </strong>

//                     {a.actor &&
//                       ` · ${a.actor}`}

//                     {a.message && (
//                       <div
//                         style={{
//                           marginTop:
//                             "8px"
//                         }}
//                       >
//                         {a.message}
//                       </div>
//                     )}

//                   </li>

//                 ))}

//             </ul>


//             {/* UI ONLY */}

//             <div
//               style={{
//                 marginTop: "24px"
//               }}
//             >

//               <h3
//                 style={{
//                   marginBottom:
//                     "12px"
//                 }}
//               >
//                 Reply to Slack Thread
//               </h3>


//               <textarea
//                 placeholder="Type reply here..."
//                 rows={3}
//                 style={{
//                   width: "100%",
//                   background:
//                     "#2a3146",
//                   border:
//                     "1px solid rgba(255,255,255,.08)",
//                   borderRadius:
//                     "10px",
//                   padding:
//                     "14px",
//                   color:
//                     "white",
//                   resize:
//                     "none",
//                   fontSize:
//                     "14px"
//                 }}
//               />


//               <button
//                 style={{
//                   marginTop:
//                     "14px",
//                   background:
//                     "#2f6fed",
//                   border:
//                     "none",
//                   color:
//                     "white",
//                   padding:
//                     "10px 22px",
//                   borderRadius:
//                     "10px",
//                   cursor:
//                     "pointer"
//                 }}
//               >

//                 Send Reply

//               </button>

//             </div>

//           </div>

//       )}



//       <div className="detail-actions">

//         {ticket.jira_issue_key && (

//           <a
//             href={jiraUrl}
//             target="_blank"
//             rel="noreferrer"
//             className="jira-btn"
//           >

//             Open in Jira (
//             {ticket.jira_issue_key}
//             )

//           </a>

//         )}



//         {ticket.status !==
//           "resolved" && (

//           <button
//             className="resolve-btn"
//             onClick={() =>
//               onResolve(ticket)
//             }
//           >

//             Resolve Ticket

//           </button>

//         )}

//       </div>

//     </div>
//   );
// }

// export default TicketDetail;

import React, { useState } from "react";
import StatusBadge from "./StatusBadge";
import { replyToSlack } from "../services/api";

function TicketDetail({ ticket, onResolve }) {

  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  if (!ticket) {
    return (
      <div className="ticket-detail empty-detail">
        <p>Select a Ticket to View Details</p>
      </div>
    );
  }

  const displayId =
    ticket.jira_issue_key ||
    `TICKET-${(ticket.id || "").substring(0, 8)}`;

  const jiraBaseUrl =
    "https://grow-simplee.atlassian.net";

  const jiraUrl = ticket.jira_issue_key
    ? `${jiraBaseUrl}/browse/${ticket.jira_issue_key}`
    : ticket.jira_url || "#";


  async function handleReply() {

    if (!reply.trim()) return;

    try {

      setSending(true);

      await replyToSlack(
        ticket.id,
        reply
      );

      setReply("");

      window.location.reload();

    } catch (err) {

      console.error(err);
      alert("Failed sending reply");

    } finally {

      setSending(false);

    }
  }



  return (

<div className="ticket-detail">

<div className="detail-header">

<div>
<h2>{displayId}</h2>
<p className="detail-subtitle">
{ticket.summary}
</p>
</div>

<StatusBadge status={ticket.status}/>
</div>



<div className="detail-grid">

<p><strong>Brand:</strong> {ticket.brand}</p>

<p>
<strong>Sender Email:</strong>
{ticket.sender_email}
</p>

<p>
<strong>Source:</strong>
{ticket.source}
</p>

<p>
<strong>Issue Type:</strong>
{ticket.issue_type || "Other"}
</p>

<p>
<strong>AWB:</strong>
{ticket.awb || "N/A"}
</p>

<p>
<strong>Assigned To:</strong>
{ticket.assigned_to || "Unassigned"}
</p>

<p>
<strong>Created At:</strong>

{ticket.created_at
? new Date(ticket.created_at)
.toLocaleString()
: "N/A"}

</p>

</div>



<div className="detail-section">

<h3>Full Message</h3>

<p className="message-content">
{ticket.display_message ||
ticket.full_message}
</p>

</div>



<div className="detail-section">

<h3>Latest Jira Comment</h3>

<p>
{ticket.latest_comment ||
"No comments yet."}
</p>

</div>



{ticket.activity_history &&
ticket.activity_history.length>0 && (

<div className="detail-section">

<h3>

Activity History

{ticket.reopen_count
? ` (reopened ${ticket.reopen_count}×)`
: ""}

</h3>


<ul
style={{
listStyle:"none",
padding:0
}}
>

{ticket.activity_history
.slice(-10)
.reverse()
.map((a,idx)=>(

<li
key={idx}

style={{

marginBottom:"12px",
padding:"12px",
background:"#1b2133",

borderLeft:
"3px solid #35baf6",

borderRadius:"8px"

}}

>

<small>

{a.timestamp
? new Date(
a.timestamp
).toLocaleString()
: ""}

</small>

<strong>
{" "}
{a.event}
</strong>

{a.actor &&
` · ${a.actor}`}

{a.message && (

<div
style={{
marginTop:"8px"
}}
>

{a.message}

</div>

)}

</li>

))}

</ul>

</div>

)}



{/* ALWAYS SHOW REPLY BOX */}

<div
style={{
marginTop:"24px"
}}
>

<h3
style={{
marginBottom:"12px"
}}
>

Reply to Slack Thread

</h3>



<textarea

value={reply}

onChange={(e)=>
setReply(
e.target.value
)
}

placeholder=
"Type reply here..."

rows={3}

style={{

width:"100%",
background:"#2a3146",

border:
"1px solid rgba(255,255,255,.08)",

borderRadius:"10px",

padding:"14px",

color:"white",

resize:"none"

}}

/>



<button

onClick={handleReply}

disabled={sending}

style={{

marginTop:"14px",

background:"#2f6fed",

border:"none",

padding:
"10px 22px",

borderRadius:"10px",

color:"white",

cursor:"pointer"

}}

>

{sending
? "Sending..."
: "Send Reply"}

</button>

</div>



<div className="detail-actions">

{ticket.jira_issue_key && (

<a

href={jiraUrl}

target="_blank"

rel="noreferrer"

className="jira-btn"

>

Open in Jira
({ticket.jira_issue_key})

</a>

)}



{ticket.status !==
"resolved" && (

<button

className="resolve-btn"

onClick={()=>
onResolve(ticket)
}

>

Resolve Ticket

</button>

)}

</div>

</div>

);

}

export default TicketDetail;