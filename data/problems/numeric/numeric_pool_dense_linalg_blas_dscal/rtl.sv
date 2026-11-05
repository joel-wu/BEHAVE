module vector_scale_16x32_burst (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_alpha,
    input  logic [15:0] in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [4:0]  out_count,

    output logic        x_req_valid,
    output logic        x_req_we,
    output logic [15:0] x_req_addr,
    output logic [31:0] x_req_wdata,
    input  logic        x_req_ready,
    input  logic        x_resp_valid,
    input  logic [31:0] x_resp_rdata
);

    typedef enum logic [1:0] {
        ST_IDLE      = 2'd0,
        ST_READ_REQ  = 2'd1,
        ST_READ_RESP = 2'd2,
        ST_WRITE_REQ = 2'd3
    } state_t;

    state_t state;

    logic        busy;
    logic [31:0] alpha_reg;
    logic [15:0] base_addr_reg;
    logic [3:0]  index_reg;
    logic [31:0] write_data_reg;
    logic [4:0]  out_count_reg;

    logic [15:0] curr_addr;
    logic signed [31:0] alpha_s;
    logic signed [31:0] x_s;
    logic signed [63:0] prod_s;

    wire output_consumed = out_valid & out_ready;
    wire input_accepted  = in_valid & in_ready;

    assign curr_addr = base_addr_reg + index_reg;
    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy & (state == ST_IDLE);
    assign out_count = out_count_reg;

    always_comb begin
        x_req_valid = 1'b0;
        x_req_we    = 1'b0;
        x_req_addr  = 16'd0;
        x_req_wdata = 32'd0;

        unique case (state)
            ST_IDLE: begin
            end
            ST_READ_REQ: begin
                x_req_valid = 1'b1;
                x_req_we    = 1'b0;
                x_req_addr  = curr_addr;
            end
            ST_READ_RESP: begin
            end
            ST_WRITE_REQ: begin
                x_req_valid = 1'b1;
                x_req_we    = 1'b1;
                x_req_addr  = curr_addr;
                x_req_wdata = write_data_reg;
            end
        endcase
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state          <= ST_IDLE;
            busy           <= 1'b0;
            alpha_reg      <= 32'd0;
            base_addr_reg  <= 16'd0;
            index_reg      <= 4'd0;
            write_data_reg <= 32'd0;
            out_count_reg  <= 5'd0;
        end else begin
            if (input_accepted) begin
                alpha_reg      <= in_alpha;
                base_addr_reg  <= in_base_addr;
                index_reg      <= 4'd0;
                write_data_reg <= 32'd0;

                if ($signed(in_alpha) == 32'sd1) begin
                    state         <= ST_IDLE;
                    busy          <= 1'b1;
                    out_count_reg <= 5'd0;
                end else begin
                    state         <= ST_READ_REQ;
                    busy          <= 1'b1;
                    out_count_reg <= 5'd16;
                end
            end else if (output_consumed) begin
                state <= ST_IDLE;
                busy  <= 1'b0;
            end else if (busy && (state == ST_READ_REQ)) begin
                if (x_req_ready) begin
                    state <= ST_READ_RESP;
                end
            end else if (busy && (state == ST_READ_RESP)) begin
                if (x_resp_valid) begin
                    alpha_s = $signed(alpha_reg);
                    x_s     = $signed(x_resp_rdata);
                    prod_s  = alpha_s * x_s;
                    write_data_reg <= prod_s[31:0];
                    state <= ST_WRITE_REQ;
                end
            end else if (busy && (state == ST_WRITE_REQ)) begin
                if (x_req_ready) begin
                    if (index_reg == 4'd15) begin
                        state <= ST_IDLE;
                    end else begin
                        index_reg <= index_reg + 4'd1;
                        state <= ST_READ_REQ;
                    end
                end
            end
        end
    end

endmodule