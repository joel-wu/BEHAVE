module data_systems_hash_cuckoo_hash_relocate (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic in_lookup,
    input logic [63:0] in_key, in_value,
    output logic out_valid,
    input logic out_ready,
    output logic [1:0] out_status,
    output logic [63:0] out_value,
    output logic [7:0] out_slot,
    output logic table_req_valid,
    input logic table_req_ready,
    output logic table_req_we,
    output logic [7:0] table_req_addr,
    output logic [127:0] table_req_wdata,
    input logic table_resp_valid,
    input logic [127:0] table_resp_rdata
);
    localparam IDLE=0, READ_REQ=1, READ_WAIT=2, WRITE_REQ=3, RESULT=4;
    logic [2:0] state;
    logic lookup;
    logic [63:0] key, value;
    logic [7:0] address, scanned;
    logic signed [63:0] product, hashed;
    always_comb begin
        product = $signed(in_key) * 64'sd1000003;
        hashed = product % 64'sd8955327411143;
    end
    assign in_ready = state == IDLE;
    assign out_valid = state == RESULT;
    assign table_req_valid = state == READ_REQ || state == WRITE_REQ;
    assign table_req_we = state == WRITE_REQ;
    assign table_req_addr = table_req_valid ? address : 0;
    assign table_req_wdata = state == WRITE_REQ ? {value,key} : 0;
    always_ff @(posedge clk) begin
        if (rst) begin
            state<=IDLE; lookup<=0; key<=0; value<=0; address<=0; scanned<=0;
            out_status<=0; out_value<=0; out_slot<=0;
        end else case(state)
            IDLE: if(in_valid) begin
                lookup<=in_lookup; key<=in_key; value<=in_value;
                address<=hashed[7:0]; scanned<=0;
                out_status<=0; out_value<=0; out_slot<=0;
                if(in_key==64'hffffffffffffffff) begin out_status<=3;state<=RESULT;end
                else state<=READ_REQ;
            end
            READ_REQ: if(table_req_ready) state<=READ_WAIT;
            READ_WAIT: if(table_resp_valid) begin
                if(table_resp_rdata[63:0]==key ||
                   (table_resp_rdata[63:0]==64'hffffffffffffffff && (!lookup || scanned==0))) begin
                    if(lookup) begin
                        if(table_resp_rdata[63:0]==key) begin
                            out_status<=0;out_value<=table_resp_rdata[127:64];out_slot<=address;
                        end else begin out_status<=1;out_value<=0;out_slot<=0;end
                        state<=RESULT;
                    end else state<=WRITE_REQ;
                end else if(scanned==255) begin
                    out_status<=lookup ? 1 : 2;out_value<=0;out_slot<=0;state<=RESULT;
                end else begin address<=address+1'b1;scanned<=scanned+1'b1;state<=READ_REQ;end
            end
            WRITE_REQ: if(table_req_ready) begin
                out_status<=0;out_value<=value;out_slot<=address;state<=RESULT;
            end
            RESULT: if(out_ready) begin state<=IDLE;out_status<=0;out_value<=0;out_slot<=0;end
            default: state<=IDLE;
        endcase
    end
endmodule
